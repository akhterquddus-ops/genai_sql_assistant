"""
test_ollama.py - Phase 4 check: Python -> Ollama -> local LLM

Run from the project folder (with Ollama running):
    python test_ollama.py

Tests the model on its own, before it is connected to the app.
"""

import logging

from config import ConfigError, get_ollama_settings
from ollama_client import OllamaError, ask, get_status, list_models

logging.basicConfig(level=logging.INFO, format="   [log] %(message)s")

# A deliberately SIMPLE prompt. Phase 5 replaces it with a robust one.
SIMPLE_SYSTEM_PROMPT = (
    "You are a SQL Server assistant. Write one T-SQL SELECT query that answers "
    "the question using only the given schema. Reply with the SQL only."
)
SCHEMA = "Customers(CustomerID, FirstName, LastName, City, Country)"


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    step("1. Load Ollama settings")
    try:
        settings = get_ollama_settings()
    except ConfigError as exc:
        print(f"FAIL: {exc}")
        return
    print(f"PASS: {settings}")

    step("2. Is Ollama running?")
    status = get_status(settings)
    if not status.running:
        print(f"FAIL: {status.message}")
        return
    print(f"PASS: Ollama version {status.version}")

    step("3. Installed models")
    for m in list_models(settings):
        print(f"   - {m['name']}  ({m['size_gb']} GB)")
    if not status.model_available:
        print(f"FAIL: {status.message}")
        return
    print(f"PASS: configured model '{settings.model}' is installed")

    try:
        step("4. First answer (includes loading the model into memory)")
        result = ask("Reply with exactly: Hello from a local LLM!")
        print(f"   Model said: {result.content}")
        print(f"PASS: {result.elapsed_seconds:.1f}s total, of which "
              f"{result.load_seconds:.1f}s was loading the model")

        step("5. First Text-to-SQL test")
        question = "Show customers from Islamabad."
        result = ask(f"Schema:\n{SCHEMA}\n\nQuestion: {question}",
                     system=SIMPLE_SYSTEM_PROMPT)
        print(f"   Question: {question}\n   Model output:\n")
        print("      " + result.content.replace("\n", "\n      "))
        print(f"\nPASS: {result.elapsed_seconds:.1f}s, "
              f"{result.output_tokens} tokens at {result.tokens_per_second:.1f} tokens/s")

        step("6. Hallucination test: ask for data that does NOT exist")
        question = "Show each customer's phone number."
        result = ask(f"Schema:\n{SCHEMA}\n\nQuestion: {question}",
                     system=SIMPLE_SYSTEM_PROMPT)
        print(f"   Question: {question}   (there is NO phone column)\n   Model output:\n")
        print("      " + result.content.replace("\n", "\n      "))
        if "phone" in result.content.lower():
            print("\nOBSERVE: the model used a 'phone' column that does not exist. "
                  "This is hallucination. Phases 5-6 defend against it.")
        else:
            print("\nOBSERVE: the model avoided inventing a column this time.")

    except OllamaError as exc:
        print(f"FAIL: {exc}")
        return

    step("Speed guide")
    speed = result.tokens_per_second
    if speed >= 15:
        verdict = "fast - you could try a larger model later"
    elif speed >= 5:
        verdict = "fine for this project"
    else:
        verdict = "slow - consider a smaller model (e.g. qwen2.5-coder:1.5b)"
    print(f"   {speed:.1f} tokens/s -> {verdict}")

    print("\nPhase 4 complete.")


if __name__ == "__main__":
    main()
