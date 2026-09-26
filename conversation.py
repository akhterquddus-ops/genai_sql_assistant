"""
conversation.py - Conversation memory for follow-up questions.

    User: How many orders were placed in January?     -> answered
    User: What about February?                        -> ???

An LLM has NO memory of its own: every request starts from zero. "Memory"
in a chat application means the APPLICATION stores earlier messages and
sends the relevant ones again with each new request.

Our approach ("query rewriting"):

    history (last few turns) + new message
         |
         v
    local LLM: rewrite into ONE standalone question
         |          "How many orders were placed in February?"
         v
    normal Text-to-SQL pipeline (unchanged, already tested)

Design choices:
  * Only the last MAX_HISTORY_TURNS turns are sent: small models get
    confused by long histories, and every extra token costs time.
  * History stores the REWRITTEN (standalone) questions, so a chain of
    follow-ups ("February?" -> "and March?") never drifts.
  * If rewriting fails for any reason, we fall back to the user's original
    words: memory is a convenience, it must never break the app.
  * History lives only in this browser session (st.session_state). It is
    not written to disk, and "New conversation" deletes it.
"""

import copy
import json
import re
import time
from dataclasses import dataclass

from config import get_ollama_settings
from ollama_client import OllamaError, chat
from prompts import (FOLLOW_UP_TYPES, REWRITE_RESPONSE_SCHEMA, REWRITE_SYSTEM_PROMPT,
                     build_rewrite_message)

MAX_HISTORY_TURNS = 3         # how many earlier turns the model sees
MAX_STORED_TURNS = 20         # how many turns we keep in the session at all


@dataclass
class RewriteResult:
    original: str
    standalone: str
    rewritten: bool            # True if the question was changed
    follow_up_type: str = "new_topic"   # replace / narrow / combine / answer / new_topic
    seconds: float = 0.0
    error: str | None = None   # set when we fell back to the original


def make_turn(original: str, standalone: str, status: str, message: str = "") -> dict:
    """One remembered exchange. status: ok / clarify / unanswerable / failed."""
    return {"original": original, "question": standalone,
            "status": status, "message": message}


def add_turn(history: list[dict], turn: dict) -> list[dict]:
    return (history + [turn])[-MAX_STORED_TURNS:]


def format_history(history: list[dict]) -> str:
    """Compact text version of the last turns, oldest first."""
    lines = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        lines.append(f"User: {turn['question']}")
        if turn["status"] == "clarify" and turn["message"]:
            lines.append(f"Assistant asked: {turn['message']}")
        elif turn["status"] == "unanswerable" and turn["message"]:
            lines.append(f"Assistant said: {turn['message']}")
    return "\n".join(lines)


# Words that genuinely ask to COMBINE two questions.
_COMBINE_WORDS = re.compile(r"\b(also|as well|both|compare|comparison|together|along with|versus|vs)\b",
                            re.IGNORECASE)

# A standalone question must not talk back to the user.
_TALKS_TO_USER = re.compile(r"\b(would you like|do you want|could you (specify|clarify)|please specify)\b",
                            re.IGNORECASE)


# --- Routing: skip the LLM when a question is obviously complete -------------
# Test lesson: asked "How many employees work in the IT department?" after a
# question about products, the 3B model merged the two into nonsense. A long
# question that starts like a question and contains no word pointing back to
# earlier messages does not need rewriting at all (and skipping saves seconds).
_POINTS_BACK = re.compile(r"\b(them|those|these|they|their|that|this|same|ones|other|others|"
                          r"previous|above|there|instead)\b", re.IGNORECASE)
_POINTS_BACK_IT = re.compile(r"\b(it|its)\b")      # lower-case only: "IT department" is not "it"
_FOLLOW_UP_START = re.compile(r"^\s*(and|but|what about|how about|only|also|then|now|except)\b",
                              re.IGNORECASE)
_QUESTION_START = re.compile(r"^\s*(how|what|which|who|when|where|show|list|give|count|find|display)\b",
                             re.IGNORECASE)
MIN_STANDALONE_WORDS = 6


def looks_standalone(question: str) -> bool:
    return (len(question.split()) >= MIN_STANDALONE_WORDS
            and bool(_QUESTION_START.match(question))
            and not _FOLLOW_UP_START.match(question)
            and not _POINTS_BACK.search(question)
            and not _POINTS_BACK_IT.search(question))


def allowed_follow_up_types(question: str, history: list[dict]) -> list[str]:
    """Which follow-up types make sense HERE? Decided by Python, not the model.

    Testing showed qwen2.5-coder:3b choosing "combine" for "And the bottom 5?"
    and even for a brand-new topic, despite a clear rule in the prompt. Python
    can check that rule perfectly, and structured output then makes the
    forbidden choices impossible for the model to pick.
    """
    allowed = list(FOLLOW_UP_TYPES)
    if not _COMBINE_WORDS.search(question):
        allowed.remove("combine")
    if history and history[-1]["status"] == "clarify":
        allowed = [t for t in allowed if t in ("answer", "new_topic")]
    else:
        allowed.remove("answer")
    return allowed


def _schema_for(allowed: list[str]) -> dict:
    schema = copy.deepcopy(REWRITE_RESPONSE_SCHEMA)
    schema["properties"]["follow_up_type"]["enum"] = allowed
    return schema


def rewrite_question(question: str, history: list[dict]) -> RewriteResult:
    """Turn a follow-up into a standalone question (or return it unchanged)."""
    question = question.strip()
    if not history:                                   # first question: nothing to resolve
        return RewriteResult(question, question, rewritten=False)
    if history[-1]["status"] != "clarify" and looks_standalone(question):
        return RewriteResult(question, question, rewritten=False)   # routing: no LLM call

    start = time.perf_counter()
    try:
        reply = chat(
            [{"role": "user", "content": build_rewrite_message(format_history(history), question)}],
            system=REWRITE_SYSTEM_PROMPT,
            response_format=_schema_for(allowed_follow_up_types(question, history)),
            max_tokens=150,
            model=get_ollama_settings().rewrite_model,
        )
        data = json.loads(reply.content)
        standalone = str(data.get("standalone_question", "")).strip()
        follow_up_type = str(data.get("follow_up_type", "new_topic")).strip()
    except OllamaError as exc:
        return RewriteResult(question, question, False, seconds=time.perf_counter() - start,
                             error=f"Follow-up could not be interpreted ({exc}); "
                                   "the question was used as typed.")
    except (json.JSONDecodeError, AttributeError) as exc:
        return RewriteResult(question, question, False, seconds=time.perf_counter() - start,
                             error=f"Follow-up could not be interpreted ({type(exc).__name__}); "
                                   "the question was used as typed.")
    seconds = time.perf_counter() - start

    # Sanity checks: an empty, rambling, user-directed or double question is
    # worse than none. ("...by sales? And the bottom 5?" = two questions.)
    if (not standalone or len(standalone) > 3 * len(question) + 200
            or _TALKS_TO_USER.search(standalone)
            or re.search(r"\?\s*\S", standalone)):
        return RewriteResult(question, question, False, seconds=seconds,
                             error="The rewrite looked wrong; the question was used as typed.")

    changed = _normalise(standalone) != _normalise(question)
    return RewriteResult(question, standalone, changed, follow_up_type, seconds)


def _normalise(text: str) -> str:
    return " ".join(text.lower().strip(" .?!").split())
