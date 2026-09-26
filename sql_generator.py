"""
sql_generator.py - Turn a plain-English question into SQL with the local LLM.

PHASE 5 VERSION (replaces the Phase 3 placeholder):

    question
       |
       v
    system prompt = rules + live schema (schema.py + prompts.py)
       |
       v
    Ollama -> local model  (answer forced into JSON: structured output)
       |
       v
    GenerationResult(status, sql, message)

status is one of:
    "ok"            -> sql contains a query
    "clarify"       -> the question was too vague; message asks for details
    "unanswerable"  -> the data does not exist; message explains what is missing

This module only WRITES SQL. It never runs it. Deciding whether SQL is safe
to run is a separate job (the validator in Phase 6).
"""

import json
import re
from dataclasses import dataclass

from config import get_ollama_settings
from ollama_client import OllamaError, chat
from prompts import SQL_RESPONSE_SCHEMA, build_system_prompt, build_user_message
from schema import get_schema_text


class SQLGenerationError(Exception):
    """A technical failure: Ollama unavailable, unreadable answer, ..."""


@dataclass
class GenerationResult:
    status: str               # "ok", "clarify" or "unanswerable"
    sql: str                  # empty unless status == "ok"
    message: str              # short explanation for the user
    raw_output: str           # exactly what the model returned (for teaching/debugging)
    system_prompt: str        # exactly what we sent (for teaching/debugging)
    model: str
    elapsed_seconds: float
    prompt_tokens: int
    output_tokens: int


# The example questions from the project brief, shown as buttons in the app.
EXAMPLE_QUESTIONS: list[str] = [
    "Show all customers from Islamabad.",
    "How many customers are registered in Pakistan?",
    "Show the 10 most expensive products.",
    "Which products have less than 10 items in stock?",
    "How many orders were placed last month?",
    "What is the total sales amount?",
    "Show total sales by month.",
    "Which customers have placed the most orders?",
    "What are the top 5 products by sales?",
    "Show employees working in the Sales department.",
    "What is the average employee salary?",
    "Show the number of employees in each department.",
    "Which city has the highest number of customers?",
    "Show orders above 1000.",
    "Compare sales between January and February.",
    "Give me a summary of this month's sales.",
]


def generate_sql(question: str) -> GenerationResult:
    question = question.strip()
    if not question:
        raise SQLGenerationError("The question is empty.")

    try:
        schema_text = get_schema_text()
    except Exception as exc:
        raise SQLGenerationError(f"Could not read the database schema: {exc}") from exc

    system_prompt = build_system_prompt(schema_text)
    try:
        reply = chat(
            [{"role": "user", "content": build_user_message(question)}],
            system=system_prompt,
            response_format=SQL_RESPONSE_SCHEMA,
        )
    except OllamaError as exc:
        raise SQLGenerationError(str(exc)) from exc

    status, sql, message = parse_model_output(reply.content)
    return GenerationResult(
        status=status, sql=sql, message=message,
        raw_output=reply.content, system_prompt=system_prompt,
        model=get_ollama_settings().model,
        elapsed_seconds=reply.elapsed_seconds,
        prompt_tokens=reply.prompt_tokens,
        output_tokens=reply.output_tokens,
    )


# ---------------------------------------------------------------------------
# Reading the model's answer
# Even with structured output we check everything: never trust model output.
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text.strip()).strip()


def parse_model_output(raw: str) -> tuple[str, str, str]:
    """Return (status, sql, message) or raise SQLGenerationError."""
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: take the first {...} block if the model added extra text.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise SQLGenerationError("The model did not return valid JSON.")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise SQLGenerationError("The model did not return valid JSON.") from exc

    if not isinstance(data, dict):
        raise SQLGenerationError("The model's answer has the wrong shape.")

    status = str(data.get("status", "")).strip().lower()
    sql = _strip_fences(str(data.get("sql", "") or ""))
    message = str(data.get("message", "") or "").strip()

    if status not in ("ok", "clarify", "unanswerable"):
        raise SQLGenerationError(f"The model returned an unknown status: '{status}'.")
    if status == "ok" and not sql:
        raise SQLGenerationError("The model said 'ok' but returned no SQL.")
    if status != "ok":
        sql = ""                     # never run SQL attached to a refusal
        if not message:
            message = ("Please rephrase your question with more detail."
                       if status == "clarify"
                       else "The database does not contain that information.")
    return status, sql, message
