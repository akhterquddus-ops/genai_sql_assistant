"""
result_analyzer.py - Explain query results in plain English with the local LLM.

    results (DataFrame)
         |
         v
    build_facts()   <- Python does the maths: row count, totals, averages...
         |             and removes sensitive columns (data minimisation)
         v
    local LLM       <- only WRITES: turns facts into 2-4 readable sentences
         |
         v
    check_numbers() <- every number in the explanation must appear in the
                       facts; anything else is flagged as possibly invented

Why this design?
  * LLMs are good at language, bad at arithmetic. A 3B model asked to "add
    up these 25 numbers" will often produce a confident, wrong total.
  * Sending thousands of rows would be slow and overflow the context window,
    so we send at most MAX_ROWS_IN_PROMPT rows plus precomputed statistics.
  * The explanation is presented as AI-generated. The table is the truth.
"""

import datetime as dt
import math
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import pandas as pd

from config import get_currency
from ollama_client import OllamaError, chat, chat_stream
from prompts import build_explanation_message, build_explanation_system_prompt

MAX_ROWS_IN_PROMPT = 20      # rows the model sees
MAX_COLUMNS_IN_PROMPT = 8
MAX_CELL_CHARS = 40
MAX_EXPLANATION_TOKENS = 200 # keeps answers short and fast

# Columns whose values are never sent to the model (data minimisation).
SENSITIVE_COLUMNS = {"email"}


class ExplanationError(Exception):
    """The explanation could not be produced (e.g. Ollama unavailable)."""


@dataclass
class Explanation:
    text: str
    seconds: float
    unverified_numbers: list[str] = field(default_factory=list)
    hidden_columns: list[str] = field(default_factory=list)
    used_llm: bool = True


# ---------------------------------------------------------------------------
# 1. Facts: what the model is allowed to know
# ---------------------------------------------------------------------------

def _fmt_number(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}"
    return f"{value:.2f}"


def _fmt_cell(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _fmt_number(value)
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS - 1] + "…"


def _is_id_column(name: str) -> bool:
    return name.lower().endswith("id")


def _is_date_column(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    sample = series.dropna()
    return not sample.empty and isinstance(sample.iloc[0], (dt.date, dt.datetime))


def _label_column(df: pd.DataFrame) -> str | None:
    """The column that names each row: the first text or date column."""
    for col in df.columns:
        if _is_id_column(col):
            continue
        series = df[col].dropna()
        if not series.empty and (_is_date_column(series)
                                 or not pd.api.types.is_numeric_dtype(series)):
            return col
    return None


def _describe_label(value) -> str:
    """A first-of-month date 2026-07-01 becomes 'July 2026', so the model
    does not have to convert dates into month names itself."""
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)) and value.day == 1:
        return value.strftime("%B %Y")
    return _fmt_cell(value)


def build_facts(df: pd.DataFrame, truncated: bool = False) -> tuple[str, list[str]]:
    """Return (facts text for the prompt, list of hidden sensitive columns).

    Lessons from testing with qwen2.5-coder:3b:
      * "ROWS RETURNED: 1" made the model answer "1 customer" when the real
        answer was 18. So a one-row result is presented as THE ANSWER.
      * A bare "total 181034" was reported as "total for 2025" although it
        covered 14 months. So every statistic states what it covers, and
        date columns state their range.
    """
    hidden = [c for c in df.columns if c.lower() in SENSITIVE_COLUMNS]
    visible = df.drop(columns=hidden)
    extra_columns = max(0, len(visible.columns) - MAX_COLUMNS_IN_PROMPT)
    visible = visible.iloc[:, :MAX_COLUMNS_IN_PROMPT]
    total_rows = len(df)
    lines: list[str] = []

    # --- One row: this row IS the answer (e.g. a count or an average) -------
    if total_rows == 1:
        lines.append("THE QUERY RETURNED A SINGLE ROW. THESE VALUES ARE THE ANSWER:")
        row = visible.iloc[0]
        for col in visible.columns:
            lines.append(f"- {col}: {_fmt_cell(row[col])}")
        if hidden:
            lines.append(f"(Sensitive columns not shown: {', '.join(hidden)})")
        return "\n".join(lines), hidden

    # --- Several rows ------------------------------------------------------------
    shown = visible.head(MAX_ROWS_IN_PROMPT)
    lines.append(f"NUMBER OF ROWS IN THE RESULT: {total_rows}"
                 + (" (row limit reached; more rows exist)" if truncated else ""))
    if len(shown) < total_rows:
        lines.append(f"ROWS SHOWN BELOW: the first {len(shown)} of {total_rows}")
    if hidden:
        lines.append(f"HIDDEN COLUMNS (sensitive, not shown): {', '.join(hidden)}")
    if extra_columns:
        lines.append(f"({extra_columns} more columns not shown)")

    lines.append("")
    lines.append("DATA:")
    lines.append(" | ".join(shown.columns))
    for row in shown.itertuples(index=False):
        lines.append(" | ".join(_fmt_cell(v) for v in row))

    # Statistics computed by Python over ALL returned rows (not just those shown).
    # Test lesson: asked "which month was highest?", the 3B model picked a real
    # value from the wrong row. So Python also names the row holding the
    # smallest and largest value; the model only has to copy it.
    label_col = _label_column(visible)
    stats = []
    for col in visible.columns:
        series = visible[col].dropna()
        if series.empty or _is_id_column(col):
            continue
        if _is_date_column(series):
            stats.append(f"- {col}: covers {_fmt_cell(series.min())} to {_fmt_cell(series.max())}")
        elif pd.api.types.is_numeric_dtype(series):
            smallest = _fmt_number(series.min())
            largest = _fmt_number(series.max())
            if label_col:
                smallest += f" ({_describe_label(visible.loc[series.idxmin(), label_col])})"
                largest += f" ({_describe_label(visible.loc[series.idxmax(), label_col])})"
            stats.append(
                f"- {col}: sum of all {len(series)} rows = {_fmt_number(series.sum())}; "
                f"average per row = {_fmt_number(series.mean())}; "
                f"smallest = {smallest}; largest = {largest}"
            )
    if stats:
        lines.append("")
        scope = "ALL returned rows" if not truncated else "the returned rows only"
        lines.append(f"STATISTICS (computed by Python over {scope}; copy them, do not recalculate):")
        lines.extend(stats)

    return "\n".join(lines), hidden


# ---------------------------------------------------------------------------
# 2. Verification: did the model invent numbers?
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def _numbers_in(text: str) -> list[float]:
    found = []
    for match in _NUMBER.findall(text):
        try:
            found.append(float(match.replace(",", "")))
        except ValueError:
            pass
    return found


_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)   # "1. ", "2) " at line start


def check_numbers(explanation: str, facts: str, question: str) -> list[str]:
    """Return numbers in the explanation that do not appear in the facts/question.

    Tolerant of formatting (30,000 vs 30000.00) and small rounding (4,938 vs 4938.24).
    Ignores list numbering ("1. Laptop ...").

    LIMITATION: this only proves a number EXISTS in the data, not that it is
    used correctly ("1 customer" when 1 was the row count would pass).
    """
    explanation = _LIST_MARKER.sub(" ", explanation)
    allowed = _numbers_in(facts) + _numbers_in(question)
    suspicious = []
    for match in _NUMBER.findall(explanation):
        try:
            value = float(match.replace(",", ""))
        except ValueError:
            continue
        ok = any(math.isclose(value, a, rel_tol=0.005, abs_tol=0.51) for a in allowed)
        if not ok and match not in suspicious:
            suspicious.append(match)
    return suspicious


# ---------------------------------------------------------------------------
# 3. Currency
# The prompt tells the model the currency. Small models still sometimes write
# "$" out of habit, so any "$" left is replaced here as a safety net.
# ---------------------------------------------------------------------------

_AMOUNT = r"(\d[\d,]*(?:\.\d+)?)"


def localise_currency(text: str) -> str:
    """'$28,750', '28,750 USD', '28,750 dollars' -> 'Rs. 28,750'."""
    symbol = get_currency()[0]
    text = re.sub(_AMOUNT + r"\s*(?:USD|US\s*dollars?|dollars?)\b", rf"{symbol} \1", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\bUSD\s*", f"{symbol} ", text)
    text = re.sub(r"\$\s*", f"{symbol} ", text)
    return text


def _system_prompt() -> str:
    return build_explanation_system_prompt(*get_currency())


# ---------------------------------------------------------------------------
# 4. Asking the model
# ---------------------------------------------------------------------------

def _no_rows_explanation(question: str) -> Explanation:
    return Explanation(
        text="The query ran successfully but found no matching rows, so there is "
             "nothing to summarise. Try widening the question (for example a "
             "longer date range).",
        seconds=0.0, used_llm=False)


def explain(question: str, df: pd.DataFrame, truncated: bool = False) -> Explanation:
    """Non-streaming version (used by the test script)."""
    if df.empty:
        return _no_rows_explanation(question)
    facts, hidden = build_facts(df, truncated)
    start = time.perf_counter()
    try:
        reply = chat([{"role": "user", "content": build_explanation_message(question, facts)}],
                     system=_system_prompt(), max_tokens=MAX_EXPLANATION_TOKENS)
    except OllamaError as exc:
        raise ExplanationError(str(exc)) from exc
    text = localise_currency(reply.content.strip())
    return Explanation(text, time.perf_counter() - start,
                       check_numbers(text, facts, question), hidden)


def explain_stream(question: str, df: pd.DataFrame, truncated: bool = False
                   ) -> tuple[Iterator[str], str, list[str]]:
    """Streaming version for the app.

    Returns (piece iterator, facts, hidden columns). After the iterator is
    used up, call check_numbers(full_text, facts, question) on the result.
    """
    facts, hidden = build_facts(df, truncated)
    pieces = chat_stream([{"role": "user", "content": build_explanation_message(question, facts)}],
                         system=_system_prompt(), max_tokens=MAX_EXPLANATION_TOKENS)
    return (localise_currency(p) for p in pieces), facts, hidden
