"""
test_conversation.py - Phase 8 check: does the model understand follow-ups?

Run from the project folder (Ollama running; no database needed):
    python test_conversation.py

Each case gives an earlier conversation plus a follow-up, and checks the
rewritten standalone question:
  must_include : every group needs at least one of its words
  must_exclude : none of these words may appear
"""

import logging

from config import get_ollama_settings
from conversation import make_turn, rewrite_question
from ollama_client import get_status

logging.basicConfig(level=logging.ERROR)

CASES = [
    {   # the example from the project brief
        "history": [make_turn("How many orders were placed in January?",
                              "How many orders were placed in January?", "ok")],
        "follow_up": "What about February?",
        "must_include": [["order"], ["february"]],
        "must_exclude": ["january"],
    },
    {   # narrowing a previous result
        "history": [make_turn("Show all customers from Islamabad.",
                              "Show all customers from Islamabad.", "ok")],
        "follow_up": "Only the ones who registered in 2024.",
        "must_include": [["customer"], ["islamabad"], ["2024"]],
        "must_exclude": [],
    },
    {   # a pronoun ("them") that refers back
        "history": [make_turn("Which products have less than 10 items in stock?",
                              "Which products have less than 10 items in stock?", "ok")],
        "follow_up": "Which of them is the cheapest?",
        "must_include": [["product"], ["stock", "less than 10", "fewer than 10"],
                         ["cheapest", "lowest price", "least expensive"]],
        # "and which" = two questions glued together (a real failure seen in testing)
        "must_exclude": ["them", "and which"],
    },
    {   # "the opposite" of the previous question
        "history": [make_turn("What are the top 5 products by sales?",
                              "What are the top 5 products by sales?", "ok")],
        "follow_up": "And the bottom 5?",
        "must_include": [["product"], ["sales"], ["bottom", "lowest", "least", "worst"]],
        "must_exclude": ["top"],
    },
    {   # answering the assistant's clarifying question
        "history": [make_turn("Show me the numbers.", "Show me the numbers.", "clarify",
                              "Which numbers would you like to see: sales, orders, products or employees?")],
        "follow_up": "orders per month",
        "must_include": [["order"], ["month"]],
        # "Which order per month would you like to see?" talks back to the user
        "must_exclude": ["would you"],
    },
    {   # a chain of follow-ups: history holds the already-rewritten question
        "history": [make_turn("How many orders were placed in January?",
                              "How many orders were placed in January?", "ok"),
                    make_turn("What about February?",
                              "How many orders were placed in February?", "ok")],
        "follow_up": "And March?",
        "must_include": [["order"], ["march"]],
        "must_exclude": ["january", "february"],
    },
    {   # generalisation: "How about X?" is not in the prompt's examples
        "history": [make_turn("Show employees working in the Sales department.",
                              "Show employees working in the Sales department.", "ok")],
        "follow_up": "How about Finance?",
        "must_include": [["employee"], ["finance"]],
        "must_exclude": ["sales"],
    },
    {   # a NEW topic must stay unchanged
        "history": [make_turn("Show the 10 most expensive products.",
                              "Show the 10 most expensive products.", "ok")],
        "follow_up": "How many employees work in the IT department?",
        "must_include": [["employee"], ["it"]],
        "must_exclude": ["product", "expensive"],
    },
]


def check(text: str, must_include: list[list[str]], must_exclude: list[str]) -> list[str]:
    words = f" {text.lower()} "
    problems = []
    for group in must_include:
        if not any(w in words for w in group):
            problems.append(f"missing {' / '.join(group)}")
    for w in must_exclude:
        if f" {w}" in words:
            problems.append(f"should not contain '{w}'")
    return problems


def main() -> None:
    status = get_status()
    if not (status.running and status.model_available):
        print(f"FAIL: {status.message}")
        return
    print(f"Rewrite model: {get_ollama_settings().rewrite_model}\n")

    passed, times = 0, []
    for i, case in enumerate(CASES, start=1):
        for turn in case["history"]:
            print(f"   User: {turn['original']}")
            if turn["status"] == "clarify":
                print(f"   Assistant asked: {turn['message']}")
        print(f"   User: {case['follow_up']}")
        result = rewrite_question(case["follow_up"], case["history"])
        times.append(result.seconds)
        problems = check(result.standalone, case["must_include"], case["must_exclude"])
        if result.error:
            problems.append(result.error)
        passed += not problems
        mark = "✅" if not problems else "❌ " + "; ".join(problems)
        timing = f"{result.seconds:.1f}s" if result.seconds else "no LLM call: looks standalone"
        print(f"Q{i} [{result.follow_up_type}] → {result.standalone}\n"
              f"   {mark}  ({timing})\n")

    avg = sum(times) / len(times) if times else 0
    print(f"SCORE: {passed}/{len(CASES)} · average rewrite time {avg:.1f}s")


if __name__ == "__main__":
    main()
