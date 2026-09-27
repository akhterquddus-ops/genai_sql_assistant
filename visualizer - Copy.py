"""
visualizer.py - Choose a suitable chart for a query result (Phase 10).

The CHART TYPE is chosen by simple, explainable rules in Python, not by the
LLM. The rules are the ones a data analyst would apply:

    one row, numbers only          -> big number ("metric"), not a chart
    a date column + a number       -> LINE chart (a trend over time)
    a text column + a number       -> BAR chart (compare categories)
    no number to plot              -> no chart, the table says it all

Why not ask the LLM? Choosing a chart is a question with clear rules and a
checkable answer. Code does that instantly, the same way every time, and can
explain its reason. The LLM is kept for what only it can do: language.
(Same principle as the validator in Phase 6 and the statistics in Phase 7.)
"""

import datetime as dt
import re
from dataclasses import dataclass, field

import pandas as pd

MAX_BAR_CATEGORIES = 25       # more bars than this become unreadable
HORIZONTAL_LABEL_LENGTH = 12  # long labels read better on horizontal bars
HORIZONTAL_MIN_BARS = 8

_YEAR = re.compile(r"^(year|yr|\w*year)$", re.IGNORECASE)
_MONTH = re.compile(r"^(month|mon|\w*month)$", re.IGNORECASE)
_TIME_PART = re.compile(r"(year|month|quarter|week|day)$", re.IGNORECASE)


@dataclass
class ChartSpec:
    kind: str                         # "line", "bar", "metric" or "none"
    reason: str                       # shown to the user: why this chart
    x: str | None = None              # label / date column
    measures: list[str] = field(default_factory=list)   # numeric columns that can be plotted
    data: pd.DataFrame | None = None  # prepared data (only the needed columns)
    horizontal: bool = False
    note: str = ""                    # e.g. "showing the top 25 of 60"


# ---------------------------------------------------------------------------
# Column roles
# ---------------------------------------------------------------------------

def _is_id(col: str) -> bool:
    return col.lower().endswith("id")


def _is_date(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    sample = series.dropna()
    return not sample.empty and isinstance(sample.iloc[0], (dt.date, dt.datetime))


def _is_measure(df: pd.DataFrame, col: str) -> bool:
    """A number worth plotting: numeric, not an ID, not a year/month part."""
    series = df[col]
    return (pd.api.types.is_numeric_dtype(series)
            and not pd.api.types.is_bool_dtype(series)
            and not _is_id(col)
            and not _TIME_PART.search(col))


def _year_month_columns(df: pd.DataFrame) -> tuple[str, str] | None:
    """LLM-written SQL often returns YEAR(...) and MONTH(...) as two number columns."""
    year = next((c for c in df.columns if _YEAR.match(c) and pd.api.types.is_numeric_dtype(df[c])), None)
    month = next((c for c in df.columns if _MONTH.match(c) and pd.api.types.is_numeric_dtype(df[c])), None)
    if year and month and df[month].between(1, 12).all():
        return year, month
    return None


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def suggest_chart(df: pd.DataFrame) -> ChartSpec:
    if df.empty:
        return ChartSpec("none", "the query returned no rows")

    measures = [c for c in df.columns if _is_measure(df, c)]
    if not measures:
        return ChartSpec("none", "the result has no numeric column to plot")

    # Rule 1: a single row of numbers is clearer as big numbers
    if len(df) == 1:
        return ChartSpec("metric", "a single result is clearer as a number than as a chart",
                         measures=measures, data=df)

    # Rule 2: a date (or year + month) column -> trend over time
    date_col = next((c for c in df.columns if _is_date(df[c])), None)
    data = df.copy()
    if date_col is None and (ym := _year_month_columns(df)):
        year, month = ym
        date_col = "Period"
        data[date_col] = pd.to_datetime(dict(year=df[year], month=df[month], day=1))
    if date_col is not None:
        data[date_col] = pd.to_datetime(data[date_col])
        data = data.sort_values(date_col)[[date_col] + measures]
        if len(data) >= 3:
            return ChartSpec("line", f"'{date_col}' is a date, so the trend over time is shown",
                             x=date_col, measures=measures, data=data)
        # Two dates (e.g. "January vs February"): bars compare them better
        data["Period"] = data[date_col].dt.strftime("%b %Y")
        return ChartSpec("bar", "only two periods, so a comparison is shown",
                         x="Period", measures=measures, data=data[["Period"] + measures])

    # Rule 3: text column(s) + a number -> compare categories
    text_cols = [c for c in df.columns if not _is_id(c) and not pd.api.types.is_numeric_dtype(df[c])]
    if not text_cols:
        return ChartSpec("none", "there is no label column (only numbers)")

    label_cols = text_cols[:2]                      # e.g. FirstName + LastName
    if len(label_cols) == 1:
        label = label_cols[0]
        data = df[[label] + measures].copy()
        data[label] = data[label].astype(str)
    else:
        label = "Name" if all("name" in c.lower() for c in label_cols) else " / ".join(label_cols)
        data = df[measures].copy()
        data.insert(0, label, df[label_cols].astype(str).agg(" ".join, axis=1))

    if data[label].duplicated().any():
        return ChartSpec("none", f"the labels in '{label}' are not unique, so bars would be misleading")

    note = ""
    if len(data) > MAX_BAR_CATEGORIES:
        note = f"showing the top {MAX_BAR_CATEGORIES} of {len(data)} by {measures[0]}"
        data = data.nlargest(MAX_BAR_CATEGORIES, measures[0])

    longest = data[label].str.len().max()
    horizontal = longest > HORIZONTAL_LABEL_LENGTH or len(data) >= HORIZONTAL_MIN_BARS
    return ChartSpec("bar", f"'{label}' names categories, so they are compared as bars",
                     x=label, measures=measures, data=data, horizontal=horizontal, note=note)


def bar_sort(data: pd.DataFrame, measure: str) -> bool | str:
    """Keep the query's own order if it is already sorted by the measure
    (e.g. "top 5 ... ORDER BY TotalSales DESC"); otherwise sort largest first."""
    values = data[measure]
    if values.is_monotonic_decreasing or values.is_monotonic_increasing:
        return False
    return f"-{measure}"
