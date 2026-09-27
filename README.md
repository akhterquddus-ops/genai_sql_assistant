# 🧠 GenAI SQL Assistant

Ask questions about a database in plain English and get answers from Microsoft SQL Server, without writing SQL.

> **"What are the top 5 products by sales?"**
> → the local LLM writes the SQL → the SQL is checked for safety → SQL Server runs it
> → you see the SQL, a results table, a chart, a plain-English explanation and a CSV download.

Everything runs **on your own computer**. The language model runs locally through [Ollama](https://ollama.com), so there is **no paid API, no API key and no data leaving the machine**.

This project was built step by step for a **Generative AI class**. Every component has a test script, and every design decision is explained in the code comments.

---

## Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Running the app](#running-the-app)
6. [Configuration (.env)](#configuration-env)
7. [Project structure](#project-structure)
8. [Test scripts](#test-scripts)
9. [The ten phases (teaching guide)](#the-ten-phases-teaching-guide)
10. [Security design](#security-design)
11. [Privacy](#privacy)
12. [Troubleshooting](#troubleshooting)
13. [Known limitations](#known-limitations)
14. [Ideas for extension](#ideas-for-extension)

---

## Features

- **Natural language → SQL** with a local open-source model (`qwen2.5-coder:3b` by default)
- **Schema grounding:** the model sees the real tables, columns, keys and relationships, read live from SQL Server
- **Structured output:** the model answers in a fixed JSON format (`ok` / `clarify` / `unanswerable`)
- **Two-layer security:** a SQL validator (application level) plus a read-only database login (database level)
- **Self-correction:** honest mistakes (e.g. MySQL's `LIMIT`) are sent back to the model once with the error message; dangerous SQL is never retried
- **Results** as a table, with row limit, query timeout and CSV download
- **AI explanation** of the results, with automatic detection of invented numbers
- **Follow-up questions** ("What about February?") through query rewriting
- **Schema retrieval (RAG):** sends only the relevant tables when a database is large
- **Automatic charts:** line, bar or big-number display, chosen by rules
- **Transparency:** "Behind the scenes" panels show exactly what the model saw and said

---

## Architecture

```
                 ┌───────────────────────────────────────────────┐
  Browser  ───►  │  Streamlit web app (app.py)                   │
                 └───────────────────────┬───────────────────────┘
                                         │ question
                                         ▼
   Follow-up?  ──►  conversation.py   rewrite into a standalone question   (Phase 8)
                                         │
   Large schema? ─► schema_retriever.py  pick relevant tables (RAG)        (Phase 9)
                                         │
                    sql_generator.py  +  prompts.py  +  schema.py
                                         │
                                         ▼
                    ollama_client.py ──► Ollama ──► local LLM              (Phases 4–5)
                                         │ SQL (JSON)
                                         ▼
                    sql_validator.py  SELECT only, one statement,          (Phase 6)
                                      approved tables, T-SQL syntax
                                         │ safe SQL
                                         ▼
                    database.py ──► SQL Server  (read-only login)          (Phases 1–2)
                                         │ rows
                                         ▼
          table + chart (visualizer.py) + explanation (result_analyzer.py) (Phases 3, 7, 10)
```

**Guiding principle:** the LLM does what only an LLM can do (understand language, write SQL, write explanations). Everything that can be decided by rules is done in code, because code is fast, identical every time and can be tested. That covers checking SQL safety, calculating totals, choosing a chart, and deciding whether a question is a follow-up.

---

## Requirements

### Hardware

| | Minimum | Recommended |
|---|---|---|
| RAM | 8 GB | 16 GB |
| CPU | Modern 4-core | 6+ cores |
| GPU | **Not required** | NVIDIA GPU makes answers much faster |
| Disk | ~5 GB free | ~10 GB (to try larger models) |

With `qwen2.5-coder:3b` on a CPU-only laptop, expect about **5–15 seconds per answer**. The first question after starting is slower while the model loads.

### Software (Windows)

| Software | Purpose |
|---|---|
| **Microsoft SQL Server** 2019 or newer (Developer or Express) | The database. Developed on SQL Server 2025 |
| **SQL Server Management Studio (SSMS)** | Running the setup scripts |
| **ODBC Driver 18 for SQL Server** | Lets Python talk to SQL Server |
| **Python 3.11+** (Anaconda or python.org) | The application |
| **Ollama** | Runs the local language models |
| **Git** | Version control (optional) |

---

## Installation

All commands are for **PowerShell**. Run them one line at a time.

### 1. Get the code

```powershell
git clone https://github.com/akhterquddus-ops/genai_sql_assistant.git
cd genai_sql_assistant
```

### 2. Create the Python environment

With Anaconda:
```powershell
conda create -n genai_sql python=3.12 -y
conda activate genai_sql
pip install -r requirements.txt
```

With plain Python instead:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Create the database

In SSMS, open and **Execute** these scripts **in this order**:

| # | Script | What it does |
|---|---|---|
| 1 | `sql/create_database.sql` | Creates `GenAI_Demo_DB` with 5 tables (**drops it first if it exists**) |
| 2 | `data/sample_data.sql` | Loads fictional sample data (30 customers, 25 products, 300 orders, 20 employees) |
| 3 | `sql/create_readonly_user.sql` | Creates the read-only login `genai_reader`. **Replace the placeholder password first** |

Step 3 needs **SQL Server and Windows Authentication mode**. In SSMS: right-click the server → **Properties** → **Security** → select that mode → OK, then right-click the server → **Restart**.

Order dates in the sample data are generated **relative to the day you run the script**, so questions like "last month" always return rows.

### 4. Configure `.env`

```powershell
copy .env.example .env
notepad .env
```

Set at least `DB_PASSWORD` to the password of `genai_reader`. If the password contains special characters, put it in single quotes: `DB_PASSWORD='My#Pass'`.

> **Never commit `.env`.** It is listed in `.gitignore`. Only `.env.example` (without secrets) belongs in Git.

### 5. Install Ollama and the models

```powershell
winget install Ollama.Ollama
```
Open a **new** PowerShell window, then:
```powershell
ollama pull qwen2.5-coder:3b        # the SQL / language model (1.9 GB)
ollama pull nomic-embed-text        # the embedding model for schema retrieval (274 MB)
```

### 6. Check everything

```powershell
python test_database.py       # expect 9 × PASS, including "read-only"
python test_ollama.py         # expect the model to answer and write SQL
```

---

## Running the app

```powershell
conda activate genai_sql
streamlit run app.py
```

The browser opens at **http://localhost:8501**. Stop the app with **Ctrl+C**.

The app listens on `localhost` only (see `.streamlit/config.toml`), so other computers on the network cannot open it.

### Things to try

| Question | Shows |
|---|---|
| What are the top 5 products by sales? | JOINs, bar chart, explanation |
| Show total sales by month. | Line chart |
| How many customers are registered in Pakistan? | Single number display |
| How many orders were placed in January? → *What about February?* | Follow-up questions |
| Show each customer's phone number. | "Not available" (no hallucinated column) |
| Show me the numbers. | Clarifying question |
| Delete all cancelled orders. | Refusal: the assistant is read-only |

In **🛠️ Developer: run your own SQL**, try an attack such as
`SELECT 1 FROM dbo.Customers; DROP TABLE dbo.Customers`. The validator blocks it.

---

## Configuration (.env)

| Variable | Default | Meaning |
|---|---|---|
| `DB_SERVER` | – | SQL Server name, e.g. `localhost` or `localhost\SQLEXPRESS` |
| `DB_DATABASE` | – | `GenAI_Demo_DB` |
| `DB_AUTH_MODE` | `sql` | `sql` (username + password) or `windows` (not read-only!) |
| `DB_USERNAME` / `DB_PASSWORD` | – | The read-only login `genai_reader` |
| `DB_DRIVER` | `ODBC Driver 18 for SQL Server` | Check installed drivers: `python -c "import pyodbc; print(pyodbc.drivers())"` |
| `DB_TRUST_SERVER_CERTIFICATE` | `yes` | `yes` for a local server with a self-signed certificate |
| `DB_LOGIN_TIMEOUT` | `5` | Seconds to wait when connecting |
| `DB_QUERY_TIMEOUT` | `30` | Seconds before a query is cancelled |
| `MAX_RESULT_ROWS` | `1000` | Maximum rows returned to the app |
| `OLLAMA_URL` | `http://localhost:11434` | Must be this computer (see `OLLAMA_ALLOW_REMOTE`) |
| `OLLAMA_ALLOW_REMOTE` | `no` | Set to `yes` only to use Ollama on another machine deliberately |
| `OLLAMA_MODEL` | `qwen2.5-coder:3b` | Model for SQL and explanations |
| `OLLAMA_REWRITE_MODEL` | *(same as above)* | Optional larger model for follow-up questions, e.g. `qwen2.5-coder:7b` |
| `OLLAMA_TIMEOUT` | `180` | Seconds to wait for one answer |
| `OLLAMA_TEMPERATURE` | `0` | 0 = most predictable output |
| `OLLAMA_CONTEXT_SIZE` | `4096` | Tokens of prompt + answer |
| `CURRENCY_SYMBOL` / `CURRENCY_NAME` | `Rs.` / `Pakistani Rupees` | Currency used in AI explanations |
| `SCHEMA_RETRIEVAL` | `auto` | `auto`, `always` or `never` |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Model used for schema retrieval |
| `RETRIEVAL_TOP_K` | `4` | Most relevant tables sent (JOIN tables are added) |
| `RETRIEVAL_AUTO_THRESHOLD_TOKENS` | `1500` | In `auto` mode, retrieve only above this schema size |

---

## Project structure

```
genai_sql_assistant/
├── app.py                  Streamlit web interface (start here)
├── config.py               Loads and validates settings from .env
├── database.py             SQL Server connection, safe query execution, permission checks
├── schema.py               Reads tables, columns, keys and sample values from SQL Server
├── schema_retriever.py     Schema retrieval (RAG): embeddings, value linking, JOIN paths
├── prompts.py              All prompts sent to the LLM (SQL, explanation, follow-ups)
├── ollama_client.py        HTTP client for Ollama: chat, streaming, embeddings
├── sql_generator.py        Question → SQL (structured JSON output)
├── sql_validator.py        Checks every query before it runs
├── result_analyzer.py      Explains results; Python computes the numbers
├── conversation.py         Follow-up questions: routing + query rewriting
├── visualizer.py           Chooses a chart for each result
│
├── test_database.py        Phase 2   database layer
├── test_ollama.py          Phase 4   local LLM
├── test_text_to_sql.py     Phase 5   SQL accuracy against reference answers
├── test_sql_validator.py   Phase 6   attacks vs startswith("SELECT")
├── test_result_analyzer.py Phase 7   explanations and invented numbers
├── test_conversation.py    Phase 8   follow-up questions
├── test_schema_retrieval.py Phase 9  retrieval on a simulated 45-table schema
├── test_visualizer.py      Phase 10  chart selection rules
│
├── sql/
│   ├── create_database.sql        Database, tables, keys, constraints, indexes
│   └── create_readonly_user.sql   Read-only login genai_reader
├── data/
│   └── sample_data.sql            Fictional sample data
│
├── .streamlit/config.toml  App reachable from localhost only; no usage statistics
├── .env.example            Configuration template (copy to .env)
├── .gitignore              Keeps .env and caches out of Git
├── requirements.txt
└── README.md
```

---

## Test scripts

| Script | Needs DB | Needs Ollama | What it measures | Reference result* |
|---|:-:|:-:|---|---|
| `test_database.py` | ✅ | | Connection, row limit, errors, read-only check | 9/9 PASS |
| `test_ollama.py` | | ✅ | Model runs; first SQL; hallucination demo | Invented a `PhoneNumber` column |
| `test_text_to_sql.py` | ✅ | ✅ | SQL correctness by comparing **results** | 11/12 |
| `test_sql_validator.py` | | | 20 safe and dangerous queries | Validator 0/20 wrong; `startswith` 15/20 wrong |
| `test_result_analyzer.py` | ✅ | ✅ | Explanations; numbers not found in the data | 0/6 flagged, but always check the meaning by eye |
| `test_conversation.py` | | ✅ | Follow-ups rewritten correctly | 7/8 |
| `test_schema_retrieval.py` | ✅ | ✅ | Recall and prompt size on 45 tables | 12/14 recall, 87% smaller prompt |
| `test_visualizer.py` | | | Chart choice for 11 result shapes | 11/11 |

\* Measured with `qwen2.5-coder:3b` and `nomic-embed-text` on a CPU-only laptop. LLM results vary between models, versions and prompt changes. **Re-run the tests after every prompt change.**

Compare accuracy with and without schema retrieval:
```powershell
$env:SCHEMA_RETRIEVAL="always"; python test_text_to_sql.py; Remove-Item Env:SCHEMA_RETRIEVAL
```

Compare models (after `ollama pull qwen2.5-coder:7b`): set `OLLAMA_MODEL=qwen2.5-coder:7b` in `.env` and run the same tests.

---

## The ten phases (teaching guide)

Each phase adds one component. The Git history has one commit per phase, so students can see exactly what each phase changed.

| Phase | Component | Key concepts |
|---|---|---|
| **1** | SQL Server database, sample data, read-only login | Relational design, constraints, least privilege |
| **2** | `database.py`, `config.py` | Secrets in `.env`, timeouts, row limits, parameterised queries |
| **3** | Streamlit interface | Re-run model, `session_state`, caching, localhost binding |
| **4** | Ollama + local LLM | Local vs API LLMs, model size vs hardware, tokens/second, temperature |
| **5** | Text-to-SQL | System prompts, schema grounding, few-shot examples, structured output, execution accuracy |
| **6** | SQL validator | Why `startswith("SELECT")` fails, defence in depth, fail closed, prompt injection |
| **7** | AI explanation | Grounded generation, "Python calculates, the LLM writes", hallucination checks, streaming, data minimisation |
| **8** | Conversation memory | LLMs have no memory, query rewriting, context window, routing, constrained output |
| **9** | Schema retrieval (RAG) | Embeddings, cosine similarity, hybrid search, value linking, recall vs precision |
| **10** | Charts | Rule-based decisions, honest visualisation |

### Lessons that came out of testing

These are real results from building the project, and they make good discussion material:

- **Examples beat rules.** The prompt said "SQL Server has no LIMIT", yet the model still wrote `LIMIT 5`. Adding examples that use `TOP` and relative dates fixed most cases (7/10 → 11/12, including two new questions that were not in the examples).
- **A query that runs is not necessarily correct.** `MONTH(GETDATE()) + 1` works in September and fails in December.
- **Checking that numbers exist is not checking that they are used correctly.** The model reported "1 customer" (the row count) instead of 18. The fix was better-structured input, not a smarter model.
- **When prompt changes stop helping, check the model's capacity.** For follow-up questions, extra rules and examples changed nothing; a structured decision plus rules enforced in Python took the score from 4/8 to 7/8.
- **RAG has costs.** On the 5-table database, forced retrieval kept the same accuracy but doubled answer time, because the prompt changes on every question and cannot be cached. That is why retrieval is automatic, based on schema size.
- **Metadata quality decides retrieval quality.** A value like "Sales" (a department) wrongly matched every question about sales until common schema words were excluded.

---

## Security design

LLM-generated SQL is **never executed directly**. Every query passes through independent layers:

| Layer | Where | Protects against |
|---|---|---|
| Prompt rules | `prompts.py` | Most unwanted SQL, but can be bypassed (prompt injection), so **never trusted alone** |
| SQL validator | `sql_validator.py` | Writes, multiple statements, `SELECT INTO`, `EXEC`, `OPENROWSET`, `WAITFOR`, table locks, other databases, system tables, unapproved tables, MySQL syntax |
| Read-only login | SQL Server | Any write, even if the validator had a bug |
| Row limit + timeout | `database.py` | Huge results and long-running queries |
| No retry for dangerous SQL | `sql_generator.py` | Self-correction only retries honest mistakes (wrong dialect, unknown column); rejected attacks are never sent back to the model |
| Localhost binding | `.streamlit/config.toml` | Access from other computers |

The validator uses two techniques: a lexical scan that removes comments and strings before checking keywords, and a real SQL parser (`sqlglot`, T-SQL dialect) that finds every table, including in subqueries and CTEs. Anything it cannot analyse is rejected ("fail closed").

The app refuses to run any SQL unless the database login is **confirmed read-only**.

---

## Privacy

- The LLM, the embedding model and the database all run **on this computer**. `OLLAMA_URL` must point to `localhost` unless remote use is explicitly allowed.
- Customer emails are **never sent to the model**: not as schema examples, and not in the data used for explanations.
- Values used for schema retrieval (product names, cities) are matched **in Python only** and never sent to the model.
- Logs record **metadata only** (row counts, timings, a hash of the SQL), never questions, results or passwords.
- Conversation history exists only in the browser session; **New conversation** deletes it.
- Streamlit usage statistics are switched off.
- All sample data is **fictional**; emails use the reserved `example.com` domain.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `python` / `conda` / `ollama` not recognised | Open a **new** PowerShell window after installing. For Anaconda, use *Anaconda PowerShell Prompt* or run `conda init powershell` once |
| Prompt shows `(base)` | Run `conda activate genai_sql` before any project command |
| `Login failed` (SQLSTATE 28000) | Enable *SQL Server and Windows Authentication mode* and **restart** SQL Server. In SSMS, `EXEC xp_readerrorlog 0, 1, N'genai_reader';` shows the exact reason |
| `ODBC driver not found` | Install *ODBC Driver 18 for SQL Server* or correct `DB_DRIVER` |
| `Tables already contain data` | `sample_data.sql` refuses to load twice. To reset, run all three setup scripts again (step 3 of installation) |
| `Cannot drop database ... in use` | Stop the Streamlit app, then run `create_database.sql` (it disconnects other sessions itself) |
| App cannot log in after a database reset | Run `sql/create_readonly_user.sql` again: dropping the database also removes its user |
| `Ollama is not running` | Start Ollama from the Start menu |
| `Model ... is not downloaded` | `ollama pull <model name>` |
| First answer is very slow | Normal: the model is loading. Later answers are faster |
| `git push`: *Repository not found* | Git is logged in to a different GitHub account. Use `https://<your-username>@github.com/...` as the remote URL |
| Git warnings *LF will be replaced by CRLF* | Harmless line-ending conversion on Windows |

---

## Known limitations

- A 3B model makes mistakes on complex questions (multi-table JOINs, subtle date logic). The validator catches unsafe SQL, not wrong SQL.
- AI explanations are labelled as AI-generated. **The results table is always the source of truth.**
- Follow-up rewriting sees earlier questions, not earlier results, so "tell me more about the second one" does not work.
- The number check detects invented numbers, but not real numbers used with the wrong meaning.
- Schema retrieval quality depends on the table descriptions in `schema_retriever.py`.
- Single-user, local application: no user accounts or row-level permissions.

---

## Ideas for extension

- **Model comparison:** run all tests with `qwen2.5-coder:1.5b`, `3b` and `7b` and compare accuracy against speed
- **Better metadata:** store table descriptions as `MS_Description` extended properties in SQL Server
- **Vector storage in SQL Server 2025:** keep the schema embeddings in a `VECTOR` column
- **More evaluation data:** extend `test_text_to_sql.py` with your own questions and reference answers
- **Enterprise deployment:** user authentication, per-user permissions, audit logging, and a shared model server

---

*Built as a teaching project for an AI Essentials course. All data is fictional.*
