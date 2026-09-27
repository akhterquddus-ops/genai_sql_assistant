"""
schema_retriever.py - Schema retrieval: RAG for the database schema.

Until now every question sent ALL tables to the model. Fine for 5 tables;
impossible for 500. Retrieval-Augmented Generation (RAG) picks only the
tables that are relevant to the question:

  INDEXING (once, at start-up)
     each table -> a short text "document" (name, description, columns)
                -> embedding vector (numbers that capture its meaning)

  RETRIEVAL (for every question)
     question   -> embedding vector
                -> compare with every table vector (cosine similarity)
                -> + small bonus when the question names a table or column
                -> keep the top K tables
                -> ALWAYS add tables containing a VALUE the question mentions
                     ("Laptop Pro 14" is a ProductName -> Products table)
                -> keep the top K tables
                -> add tables needed to JOIN them (foreign-key paths)

  GENERATION
     SQL prompt contains only the retrieved tables

In production, the vectors would live in a vector database (Chroma, Qdrant,
pgvector, or SQL Server 2025's own VECTOR data type). For a few hundred
tables, a NumPy array in memory is perfectly adequate.
"""

import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from config import get_retrieval_settings
from ollama_client import OllamaError, embed
from schema import DatabaseSchema, get_schema

logger = logging.getLogger(__name__)

# Business descriptions of the tables. Retrieval quality depends heavily on
# good metadata: "Orders" alone says little, "customer purchases, sales,
# revenue" says a lot. Companies keep such descriptions in a data catalogue or
# in SQL Server "extended properties" (MS_Description).
TABLE_DESCRIPTIONS: dict[str, str] = {
    "Customers": "People who buy from us: name, email, city, country and registration date.",
    "Products": "Items we sell: product name, category, price and stock quantity (inventory level).",
    "Orders": "Customer orders (purchases): order date, status and total amount. "
              "Use for sales totals, revenue per period and numbers of orders.",
    "OrderDetails": "Line items of each order: which product, quantity sold and unit price. "
                    "Use for sales per product.",
    "Employees": "Staff of the company: department, city, hire date and salary.",
}

# JOIN expansion only connects tables we are confident about: those scoring
# within this margin of the best table (plus tables found by value match).
# Connecting weak "filler" tables would drag in even more tables.
JOIN_SCORE_MARGIN = 0.15

KEYWORD_BONUS_TABLE = 0.10     # question mentions the table's name
KEYWORD_BONUS_COLUMN = 0.05    # question mentions one of its columns
MAX_KEYWORD_BONUS = 0.20

_STOPWORDS = {"the", "and", "for", "with", "from", "show", "list", "what", "which", "how",
              "many", "much", "are", "was", "were", "who", "all", "each", "per", "top",
              "most", "than", "less", "more", "give", "this", "that", "have", "has", "id",
              "name", "date", "number", "total"}


@dataclass
class RetrievalInfo:
    mode: str                      # "full" or "retrieved"
    reason: str                    # why this mode was used
    tables: list[str]              # tables sent to the model
    total_tables: int
    scores: dict[str, float] = field(default_factory=dict)
    added_for_values: list[str] = field(default_factory=list)
    added_for_joins: list[str] = field(default_factory=list)
    seconds: float = 0.0
    prompt_tokens_full: int = 0
    prompt_tokens_sent: int = 0


def estimate_tokens(text: str) -> int:
    """Rough rule of thumb: 1 token is about 4 characters of English text."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Words and documents
# ---------------------------------------------------------------------------

def _split_name(name: str) -> list[str]:
    """'OrderDetails' -> ['order', 'details'];  'StockQuantity' -> ['stock', 'quantity']."""
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)]


def _stem(word: str) -> str:
    """Very small stemmer: customers -> customer, categories -> category."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 4 and word[-3] in "sxz":
        return word[:-2]
    if word.endswith("s") and len(word) > 3 and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", text.lower())
            if len(w) >= 3 and w not in _STOPWORDS}


def table_document(schema: DatabaseSchema, table_name: str,
                   descriptions: dict[str, str] | None = None) -> str:
    """The text that gets embedded for one table."""
    descriptions = descriptions if descriptions is not None else TABLE_DESCRIPTIONS
    table = next(t for t in schema.tables if t.name == table_name)
    columns = []
    for col in table.columns:
        text = col.name
        if col.sample_values:
            text += f" (values: {', '.join(col.sample_values[:8])})"
        columns.append(text)
    related = sorted({fk.ref_table for fk in schema.foreign_keys if fk.table == table_name}
                     | {fk.table for fk in schema.foreign_keys if fk.ref_table == table_name})
    doc = f"Table {table_name}. {descriptions.get(table_name, '')} Columns: {', '.join(columns)}."
    if related:
        doc += f" Related tables: {', '.join(related)}."
    return doc


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

class SchemaIndex:
    """Embeddings of every table, plus the foreign-key graph for JOIN paths."""

    def __init__(self, schema: DatabaseSchema, embedding_model: str,
                 descriptions: dict[str, str] | None = None):
        self.schema = schema
        self.model = embedding_model
        self.names = schema.table_names()
        self.documents = [table_document(schema, n, descriptions) for n in self.names]
        self._query_prefix, doc_prefix = _prefixes(embedding_model)

        start = time.perf_counter()
        vectors = np.array(embed([doc_prefix + d for d in self.documents], embedding_model))
        self.vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        self.index_seconds = time.perf_counter() - start

        # Keyword sets for the small exact-match bonus
        self.table_words = {n: {_stem(w) for w in _split_name(n)} for n in self.names}
        self.column_words = {
            t.name: {_stem(w) for c in t.columns for w in _split_name(c.name)} - {"id"}
            for t in schema.tables
        }
        # Value linking: known text values per table (never sent to the LLM).
        # Test lesson: the department value 'Sales' matched every question about
        # "sales" and wrongly pulled in Employees. A single-word value that is
        # also ordinary schema vocabulary is not evidence, so it is ignored.
        descriptions = descriptions if descriptions is not None else TABLE_DESCRIPTIONS
        vocabulary = set()
        for t in schema.tables:
            vocabulary |= {_stem(w) for w in _split_name(t.name)}
            vocabulary |= {_stem(w) for c in t.columns for w in _split_name(c.name)}
        for text in descriptions.values():
            vocabulary |= {_stem(w) for w in re.findall(r"[a-z]+", text.lower())}
        self.values = {
            t.name: {v.lower() for c in t.columns for v in c.lookup_values
                     if len(v) >= 3 and not (" " not in v.strip() and _stem(v.lower()) in vocabulary)}
            for t in schema.tables
        }
        # Undirected foreign-key graph
        self.graph: dict[str, set[str]] = {n: set() for n in self.names}
        for fk in schema.foreign_keys:
            if fk.table in self.graph and fk.ref_table in self.graph:
                self.graph[fk.table].add(fk.ref_table)
                self.graph[fk.ref_table].add(fk.table)
        logger.info("Schema index built: %d tables in %.2fs", len(self.names), self.index_seconds)

    def scores(self, question: str) -> dict[str, float]:
        """Similarity of every table to the question (higher = more relevant)."""
        q = np.array(embed([self._query_prefix + question], self.model)[0])
        q = q / np.linalg.norm(q)
        cosine = self.vectors @ q
        words = _words(question)
        result = {}
        for name, sim in zip(self.names, cosine):
            bonus = KEYWORD_BONUS_TABLE * len(words & self.table_words[name])
            bonus += KEYWORD_BONUS_COLUMN * len(words & self.column_words[name])
            result[name] = float(sim) + min(bonus, MAX_KEYWORD_BONUS)
        return result

    def value_matches(self, question: str) -> list[str]:
        """Tables that store a value mentioned in the question (exact phrase match)."""
        text = " " + re.sub(r"[^a-z0-9]+", " ", question.lower()) + " "
        return [name for name in self.names
                if any(f" {re.sub(r'[^a-z0-9]+', ' ', v).strip()} " in text
                       for v in self.values[name])]

    def join_path(self, start: str, goal: str) -> list[str]:
        """Shortest chain of tables connecting start and goal (breadth-first search)."""
        previous = {start: None}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node == goal:
                path = []
                while node is not None:
                    path.append(node)
                    node = previous[node]
                return path[::-1]
            for nxt in sorted(self.graph[node]):
                if nxt not in previous:
                    previous[nxt] = node
                    queue.append(nxt)
        return []                                   # not connected

    def retrieve(self, question: str, top_k: int
                 ) -> tuple[list[str], dict[str, float], list[str], list[str]]:
        """Return (tables, scores, added for values, added for joins)."""
        scores = self.scores(question)
        ranked = sorted(scores, key=scores.get, reverse=True)
        chosen = ranked[:top_k]

        # An exact value match is strong evidence: always include that table.
        by_value = [t for t in self.value_matches(question) if t not in chosen]
        chosen = chosen + by_value

        # Add the tables needed to JOIN the confident ones. Example: "customers
        # who bought a laptop" finds Customers + Products, but the SQL also needs
        # Orders and OrderDetails to connect them.
        best = scores[ranked[0]]
        confident = [t for t in chosen if scores[t] >= best - JOIN_SCORE_MARGIN or t in by_value]
        added: list[str] = []
        for i, a in enumerate(confident):
            for b in confident[i + 1:]:
                for table in self.join_path(a, b):
                    if table not in chosen and table not in added:
                        added.append(table)
        return chosen + added, scores, by_value, added


def _prefixes(model: str) -> tuple[str, str]:
    """nomic-embed-text expects task prefixes for best retrieval quality."""
    if "nomic" in model.lower():
        return "search_query: ", "search_document: "
    return "", ""


# ---------------------------------------------------------------------------
# Cached index + the function the SQL generator calls
# ---------------------------------------------------------------------------

_index_cache: dict[str, SchemaIndex] = {}


def get_index(schema: DatabaseSchema, embedding_model: str) -> SchemaIndex:
    key = embedding_model + ":" + hashlib.sha256(schema.to_prompt_text().encode()).hexdigest()
    if key not in _index_cache:
        _index_cache.clear()
        _index_cache[key] = SchemaIndex(schema, embedding_model)
    return _index_cache[key]


def clear_cache() -> None:
    _index_cache.clear()


def select_schema(question: str, mode: str | None = None) -> tuple[str, RetrievalInfo]:
    """Return (schema text for the prompt, information about what was chosen).

    Never fails: if retrieval is not possible, the full schema is used.
    """
    settings = get_retrieval_settings()
    mode = mode or settings.mode
    schema = get_schema()
    full_text = schema.to_prompt_text()
    full_tokens = estimate_tokens(full_text)
    names = schema.table_names()

    def full(reason: str) -> tuple[str, RetrievalInfo]:
        return full_text, RetrievalInfo("full", reason, names, len(names),
                                        prompt_tokens_full=full_tokens,
                                        prompt_tokens_sent=full_tokens)

    if mode == "never":
        return full("schema retrieval is switched off")
    if mode == "auto" and full_tokens <= settings.auto_threshold:
        return full(f"the full schema is small (~{full_tokens} tokens), so all tables are sent")
    if len(names) <= settings.top_k:
        return full("the database has no more tables than RETRIEVAL_TOP_K")

    start = time.perf_counter()
    try:
        index = get_index(schema, settings.embedding_model)
        tables, scores, by_value, added = index.retrieve(question, settings.top_k)
    except OllamaError as exc:
        logger.warning("Schema retrieval failed; using the full schema (%s)", exc)
        return full(f"retrieval failed ({exc}); the full schema was used")

    text = schema.to_prompt_text(only=set(tables))
    info = RetrievalInfo("retrieved", f"top {settings.top_k} tables by similarity"
                         + (" + tables with matching values" if by_value else "")
                         + (" + tables needed for JOINs" if added else ""),
                         tables, len(names), scores={k: round(v, 3) for k, v in scores.items()},
                         added_for_values=by_value, added_for_joins=added,
                         seconds=time.perf_counter() - start,
                         prompt_tokens_full=full_tokens, prompt_tokens_sent=estimate_tokens(text))
    return text, info
