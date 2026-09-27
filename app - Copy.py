"""
app.py - GenAI SQL Assistant (Streamlit web interface)

Start with:   streamlit run app.py

Pipeline (Phase 9):
    question -> (follow-up? rewrite into a standalone question)
             -> (large schema? retrieve only the relevant tables)
             -> local LLM writes SQL -> SQL validator -> read-only login
             -> SQL Server -> results -> local LLM explains the results

Phase history:
  Phase 3  page layout
  Phase 4  Ollama status in the sidebar
  Phase 5  the local LLM generates the SQL
  Phase 6  SQL validator checks every query before it runs
  Phase 7  the local LLM explains the results
  Phase 8  conversation memory for follow-up questions
  Phase 9  schema retrieval (RAG)
  Phase 10 automatic charts                                 <- this version
"""

import time

import streamlit as st

import schema
import schema_retriever
from conversation import MAX_HISTORY_TURNS, add_turn, make_turn, rewrite_question
from config import ConfigError, get_ollama_settings, get_retrieval_settings, get_settings
from database import DatabaseError, check_permissions, run_query, test_connection
from ollama_client import OllamaError
from ollama_client import get_status as get_ollama_status
from result_analyzer import check_numbers, explain, explain_stream, localise_currency
from sql_generator import EXAMPLE_QUESTIONS, SQLGenerationError, generate_sql
from sql_validator import validate_sql
from visualizer import bar_sort, suggest_chart

st.set_page_config(page_title="GenAI SQL Assistant", page_icon="🧠", layout="wide")


# ---------------------------------------------------------------------------
# Status checks (cached)
# Streamlit re-runs this whole script after EVERY click. Without caching,
# every click would reconnect to SQL Server just to redraw the sidebar.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def get_connection_status() -> tuple[bool, str]:
    return test_connection()


@st.cache_data(ttl=60, show_spinner=False)
def get_read_only_status() -> bool | None:
    """True = read-only, False = can write, None = could not check."""
    try:
        return check_permissions()["read_only"]
    except Exception:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def get_llm_status() -> dict:
    """Ollama status as a plain dict (cache-friendly)."""
    try:
        status = get_ollama_status(get_ollama_settings())
    except ConfigError as exc:
        return {"running": False, "model_available": False, "model": "?",
                "version": None, "message": str(exc)}
    return vars(status)


# ---------------------------------------------------------------------------
# The pipeline: question -> SQL -> validator -> read-only login -> results
# The outcome is stored in st.session_state so it survives re-runs.
# ---------------------------------------------------------------------------

def run_pipeline(question: str | None, sql: str | None = None) -> None:
    st.session_state.outcome_id = st.session_state.get("outcome_id", 0) + 1
    outcome = {"id": st.session_state.outcome_id,
               "asked": question, "question": question, "rewrite": None,
               "sql": sql, "generation": None, "validation": None,
               "notice": None, "result": None, "error": None, "error_stage": None}
    st.session_state.outcome = outcome

    if sql is not None:                  # hand-typed SQL: not part of the conversation
        _check_and_run(outcome)
        return

    # Step 0: follow-up? Rewrite it into a standalone question (Phase 8).
    history = st.session_state.get("history", [])
    if st.session_state.get("use_memory", True) and history:
        rewrite = rewrite_question(question, history)
        outcome["rewrite"] = vars(rewrite)
        outcome["question"] = rewrite.standalone

    # Step 1: question -> SQL with the local LLM (schema chosen by retrieval)
    try:
        generation = generate_sql(outcome["question"],
                                  retrieval_mode=SCHEMA_MODES[st.session_state.get("schema_mode", DEFAULT_SCHEMA_MODE)])
    except SQLGenerationError as exc:
        outcome["error"], outcome["error_stage"] = str(exc), "SQL generation"
        _remember(outcome, "failed")
        return
    outcome["generation"] = vars(generation)
    if generation.retrieval:
        outcome["generation"]["retrieval"] = vars(generation.retrieval)
    if generation.status != "ok":                 # clarify / unanswerable
        outcome["notice"] = (generation.status, generation.message)
        _remember(outcome, generation.status, generation.message)
        return
    outcome["sql"] = generation.sql

    _check_and_run(outcome)
    _remember(outcome, "ok" if outcome["error"] is None else "failed")


def _check_and_run(outcome: dict) -> None:
    # Step 2: application-level security - the SQL validator.
    # Every query (from the LLM or typed by hand) is checked before it runs.
    validation = validate_sql(outcome["sql"])
    outcome["validation"] = vars(validation)
    if not validation.is_valid:
        outcome["error"], outcome["error_stage"] = validation.reason, "SQL validation"
        return

    # Step 3: database-level security - the login must be read-only.
    # Two independent layers: if one ever fails, the other still protects us.
    if get_read_only_status() is not True:
        outcome["error"] = ("SQL was not executed because the database login is not "
                            "confirmed read-only. Use the genai_reader login.")
        outcome["error_stage"] = "Safety check"
        return

    # Step 4: run the validated SQL -> DataFrame
    try:
        outcome["result"] = run_query(validation.sql)
    except DatabaseError as exc:
        outcome["error"], outcome["error_stage"] = str(exc), "SQL execution"


def _remember(outcome: dict, status: str, message: str = "") -> None:
    """Add this exchange to the conversation history (Phase 8)."""
    turn = make_turn(outcome["asked"], outcome["question"], status, message)
    st.session_state.history = add_turn(st.session_state.get("history", []), turn)


def new_conversation() -> None:
    """Button callback: forget the conversation and clear the page."""
    st.session_state.history = []
    st.session_state.pop("outcome", None)
    st.session_state.question = ""


# Sidebar labels -> SCHEMA_RETRIEVAL modes (Phase 9)
SCHEMA_MODES = {
    "Automatic (by schema size)": "auto",
    "Only relevant tables (RAG)": "always",
    "All tables": "never",
}
DEFAULT_SCHEMA_MODE = "Automatic (by schema size)"


def use_example(question: str) -> None:
    """Button callback: put the example in the input box and ask it on this run."""
    st.session_state.question = question
    st.session_state.pending_question = question


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar(settings) -> None:
    with st.sidebar:
        st.header("System status")

        st.subheader("🗄️ Database")
        connected, message = get_connection_status()
        if connected:
            st.success("Connected")
        else:
            st.error("Not connected")
        st.caption(message)
        st.markdown(f"**Server:** `{settings.server}`  \n"
                    f"**Database:** `{settings.database}`")

        st.subheader("🔒 Security")
        read_only = get_read_only_status() if connected else None
        if read_only is True:
            st.success("Read-only database login")
        elif read_only is False:
            st.warning("This login can MODIFY data. Use the read-only genai_reader login.")
        else:
            st.info("Permissions could not be checked.")
        st.caption(f"Row limit: {settings.max_rows:,} · Query timeout: {settings.query_timeout}s  \n"
                   "SQL validator: active (SELECT only, approved tables)")

        st.subheader("🤖 Local LLM")
        llm = get_llm_status()
        if llm["running"] and llm["model_available"]:
            st.success("Ollama running · model ready")
        elif llm["running"]:
            st.warning("Ollama running · model missing")
        else:
            st.error("Ollama not available")
        st.caption(llm["message"])
        st.markdown(f"**Model:** `{llm['model']}`  \n"
                    "**Runs on:** this computer (no cloud, no API cost)")
        st.toggle("✍️ Explain results automatically", value=True, key="auto_explain",
                  help="Turn off to save time; you can still click 'Explain these results'.")

        st.subheader("📚 Schema sent to the model")
        st.selectbox("Schema", list(SCHEMA_MODES), key="schema_mode", label_visibility="collapsed",
                     help="RAG: find the relevant tables with embeddings and send only those.")
        try:
            rs = get_retrieval_settings()
            st.caption(f"Embedding model: `{rs.embedding_model}` · top {rs.top_k} tables "
                       f"(+ JOIN tables) · automatic above ~{rs.auto_threshold:,} tokens")
        except ConfigError as exc:
            st.caption(f"Retrieval settings problem: {exc}")

        st.subheader("💬 Conversation")
        st.toggle("Remember previous questions", value=True, key="use_memory",
                  help="Lets you ask follow-ups such as 'What about February?'")
        history = st.session_state.get("history", [])
        st.caption(f"{len(history)} question(s) so far · the model sees the last "
                   f"{MAX_HISTORY_TURNS}. Kept only in this browser session.")
        if history:
            with st.expander("Conversation so far"):
                for i, turn in enumerate(history, start=1):
                    line = f"**{i}.** {turn['original']}"
                    if turn["question"] != turn["original"]:
                        line += f"  \n→ *{turn['question']}*"
                    st.markdown(line)
        st.button("🗑️ New conversation", width="stretch", on_click=new_conversation)

        if st.button("🔄 Refresh status", width="stretch"):
            st.cache_data.clear()
            schema.clear_cache()           # re-read tables and columns too
            schema_retriever.clear_cache() # and rebuild the table embeddings
            st.rerun()


# ---------------------------------------------------------------------------
# Results area
# ---------------------------------------------------------------------------

def render_outcome() -> None:
    outcome = st.session_state.get("outcome")
    if outcome is None:
        return

    st.divider()
    if outcome["asked"]:
        st.markdown(f"**Question:** {outcome['asked']}")
    rewrite = outcome.get("rewrite")
    if rewrite and rewrite["rewritten"]:
        st.info(f"🔗 **Follow-up ({rewrite['follow_up_type']}) understood as:** "
                f"{rewrite['standalone']}  \n"
                f"Based on your previous questions ({rewrite['seconds']:.1f} s). "
                "If this is wrong, ask the full question or start a new conversation.")
    elif rewrite and rewrite["error"]:
        st.caption(f"🔗 {rewrite['error']}")

    generation = outcome["generation"]
    if generation:
        st.caption(f"🤖 {generation['model']} answered in {generation['elapsed_seconds']:.1f} s "
                   f"({generation['prompt_tokens']:,} prompt tokens + "
                   f"{generation['output_tokens']:,} output tokens)")

    render_retrieval_caption(generation)

    # The model asked for clarification or said the data does not exist
    if outcome["notice"]:
        kind, text = outcome["notice"]
        if kind == "clarify":
            st.info(f"🤔 **The assistant needs more detail:** {text}")
        else:
            st.warning(f"🚫 **Not available in this database:** {text}")
        render_behind_the_scenes(generation)
        return

    # Generated SQL
    st.subheader("Generated SQL")
    if outcome["sql"]:
        st.code(outcome["sql"], language="sql")
        if generation and generation["message"]:
            st.caption(f"Model's description: {generation['message']}")
    else:
        st.caption("No SQL was produced.")

    # Validation status
    validation = outcome["validation"]
    if validation and validation["is_valid"]:
        st.caption("🛡️ Validator passed: " + " · ".join(validation["checks"])
                   + f" · Tables: {', '.join(validation['tables'])}")

    # Execution status
    if outcome["error"]:
        if outcome["error_stage"] == "SQL validation":
            st.error(f"🛡️ **Blocked by the SQL validator:** {outcome['error']}  \n"
                     "This query was NOT sent to the database.")
        else:
            st.error(f"**{outcome['error_stage']} failed:** {outcome['error']}")
        render_behind_the_scenes(generation)
        return

    result = outcome["result"]
    st.success("Query executed successfully")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Rows returned", f"{result.row_count:,}")
    col2.metric("SQL generation (LLM)",
                f"{generation['elapsed_seconds']:.1f} s" if generation else "—")
    col3.metric("Query execution (DB)", f"{result.elapsed_seconds:.3f} s")
    col4.metric("Columns", len(result.dataframe.columns))

    if result.truncated:
        st.warning(f"Only the first {result.row_count:,} rows are shown (row limit).")

    # Results table
    st.subheader("Query results")
    if result.row_count == 0:
        st.info("The query ran successfully but returned no rows.")
    else:
        st.dataframe(result.dataframe, width="stretch", hide_index=True)
        # utf-8-sig lets Excel open non-English characters correctly.
        st.download_button(
            "⬇️ Download results as CSV",
            data=result.dataframe.to_csv(index=False).encode("utf-8-sig"),
            file_name="query_results.csv",
            mime="text/csv",
            on_click="ignore",           # downloading does not re-run the app
        )

    # Chart (Phase 10)
    render_chart(outcome, result)

    # AI explanation (Phase 7)
    st.subheader("AI explanation")
    render_explanation(outcome, result)

    render_behind_the_scenes(generation)


def render_chart(outcome: dict, result) -> None:
    """Show a chart chosen by simple rules in visualizer.py (not by the LLM)."""
    spec = suggest_chart(result.dataframe)
    if spec.kind == "none":
        st.caption(f"📊 No chart: {spec.reason}.")
        return

    st.subheader("Chart")
    measure = spec.measures[0]
    if len(spec.measures) > 1 and spec.kind != "metric":
        measure = st.selectbox("Value to plot", spec.measures, key=f"measure_{outcome['id']}")

    if spec.kind == "metric":
        columns = st.columns(min(len(spec.measures), 4))
        for col, name in zip(columns, spec.measures):
            value = spec.data[name].iloc[0]
            col.metric(name, f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float)
                       else f"{value:,}")
    elif spec.kind == "line":
        st.line_chart(spec.data, x=spec.x, y=measure)
    else:
        st.bar_chart(spec.data, x=spec.x, y=measure, horizontal=spec.horizontal,
                     sort=bar_sort(spec.data, measure))

    note = f" ({spec.note})" if spec.note else ""
    if result.truncated:
        note += " · based on the rows shown (row limit reached)"
    st.caption(f"📊 {spec.kind.capitalize()} chosen because {spec.reason}{note}.")


def _escape_markdown(text: str) -> str:
    """Streamlit treats $...$ as a maths formula; money amounts must stay text."""
    return text.replace("$", "\\$")


def render_explanation(outcome: dict, result) -> None:
    question = outcome["question"] or "Describe what these results show."
    explanation = outcome.get("explanation")

    # Not explained yet: generate now (streaming) or offer a button.
    if explanation is None:
        if result.row_count == 0:
            explanation = vars(explain(question, result.dataframe))   # no LLM needed
            explanation["facts"] = ""
        elif st.session_state.get("auto_explain", True) or st.button("✍️ Explain these results"):
            explanation = stream_explanation(question, result)
            outcome["explanation"] = explanation
            show_explanation_notes(explanation)
            return
        else:
            st.caption("Automatic explanations are switched off in the sidebar.")
            return
        outcome["explanation"] = explanation

    # Already explained (e.g. after a re-run): show the stored text.
    if explanation.get("error"):
        st.warning(f"The explanation could not be generated: {explanation['error']}")
        if st.button("↻ Try the explanation again"):
            outcome["explanation"] = None
            st.rerun()
        return
    st.markdown(_escape_markdown(explanation["text"]))
    show_explanation_notes(explanation)


def stream_explanation(question: str, result) -> dict:
    """Show the explanation word by word as the model writes it."""
    start = time.perf_counter()
    try:
        pieces, facts, hidden = explain_stream(question, result.dataframe, result.truncated)
        text = st.write_stream(_escape_markdown(p) for p in pieces)
    except OllamaError as exc:
        st.warning(f"The explanation could not be generated: {exc}")
        return {"error": str(exc)}
    if not isinstance(text, str):
        text = "".join(str(t) for t in text)
    # Final pass on the whole text (a streamed "28,750" + " dollars" is split in two).
    text = localise_currency(text.replace("\\$", "$")).strip()
    return {"text": text, "seconds": time.perf_counter() - start,
            "unverified_numbers": check_numbers(text, facts, question),
            "hidden_columns": hidden, "used_llm": True, "facts": facts}


def show_explanation_notes(explanation: dict) -> None:
    if explanation.get("error"):
        return
    if explanation.get("used_llm"):
        st.caption(f"✍️ Written by the local LLM from the table above in "
                   f"{explanation['seconds']:.1f} s. AI-generated text: the table is "
                   "the source of truth.")
    if explanation.get("unverified_numbers"):
        numbers = ", ".join(explanation["unverified_numbers"])
        st.warning(f"⚠️ **Check these numbers:** {numbers}. They do not appear in the "
                   "query results, so the model may have invented or miscalculated them.")
    if explanation.get("hidden_columns"):
        st.caption("🔒 Not shown to the model (sensitive): "
                   + ", ".join(explanation["hidden_columns"]))
    if explanation.get("facts"):
        with st.expander("🔍 What the model was given to write this explanation"):
            st.code(explanation["facts"], language="text")


def render_retrieval_caption(generation: dict | None) -> None:
    info = (generation or {}).get("retrieval")
    if not info:
        return
    if info["mode"] == "retrieved":
        joins = (f"; {', '.join(info['added_for_values'])} added by value match"
                 if info.get("added_for_values") else "")
        joins += f"; {', '.join(info['added_for_joins'])} added for JOINs" if info["added_for_joins"] else ""
        st.caption(f"📚 Tables sent to the model: **{', '.join(info['tables'])}** "
                   f"({len(info['tables'])} of {info['total_tables']}{joins}) · "
                   f"~{info['prompt_tokens_sent']:,} instead of ~{info['prompt_tokens_full']:,} "
                   f"schema tokens · retrieval {info['seconds']:.1f} s")
    else:
        st.caption(f"📚 All {info['total_tables']} tables sent: {info['reason']}.")


def render_behind_the_scenes(generation: dict | None) -> None:
    """Show exactly what was sent to and received from the LLM (for teaching)."""
    if not generation:
        return
    with st.expander("🔍 Behind the scenes: what the LLM saw and said"):
        st.markdown("**System prompt** (rules + live schema, sent with every question):")
        st.code(generation["system_prompt"], language="text")
        st.markdown("**Raw model output** (structured JSON):")
        st.code(generation["raw_output"], language="json")
        info = generation.get("retrieval")
        if info and info["scores"]:
            st.markdown("**Schema retrieval scores** (similarity to the question; higher = more relevant):")
            ranked = sorted(info["scores"].items(), key=lambda kv: kv[1], reverse=True)
            st.dataframe(
                [{"Table": t, "Score": round(v, 3), "Sent": "✅" if t in info["tables"] else ""}
                 for t, v in ranked],
                hide_index=True)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        settings = get_settings()
    except ConfigError as exc:
        st.error(f"Configuration problem: {exc}")
        st.stop()

    st.title("🧠 GenAI SQL Assistant")
    st.caption("Ask questions about the GenAI_Demo_DB database in plain English. "
               "Everything runs locally; no paid AI API.")

    # Question input. A form means pressing Enter also submits.
    with st.form("question_form"):
        question = st.text_input(
            "Ask your question:",
            key="question",
            placeholder="e.g. What are the top 5 products by sales?",
        )
        asked = st.form_submit_button("Ask", type="primary")

    # A question comes either from the form or from an example button.
    to_ask = st.session_state.pop("pending_question", None)
    if asked:
        to_ask = question.strip()
        if not to_ask:
            st.warning("Please type a question first.")

    if to_ask:
        with st.spinner("The local model is writing SQL... (the first question takes longer)"):
            run_pipeline(to_ask)

    # Results appear directly under the question box.
    render_outcome()

    # Example questions
    with st.expander("💡 Example questions (click one to ask it)"):
        cols = st.columns(2)
        for i, example in enumerate(EXAMPLE_QUESTIONS):
            cols[i % 2].button(example, key=f"example_{i}", width="stretch",
                               on_click=use_example, args=(example,))

    # Developer tool: run SQL typed by hand
    with st.expander("🛠️ Developer: run your own SQL"):
        st.caption("Your SQL goes through exactly the same checks as the LLM's: "
                   "the SQL validator, then the read-only login. Try an attack, "
                   "e.g.  SELECT 1 FROM dbo.Customers; DROP TABLE dbo.Customers")
        manual_sql = st.text_area("SQL", height=120, key="manual_sql",
                                  placeholder="SELECT TOP 5 ProductName, Price FROM dbo.Products;")
        if st.button("Run SQL"):
            if not manual_sql.strip():
                st.warning("Please type some SQL first.")
            else:
                with st.spinner("Running..."):
                    run_pipeline(None, sql=manual_sql.strip())
                st.rerun()   # redraw so the results appear under the question box

    # The sidebar is drawn LAST, so it shows the state AFTER this question ran
    # (e.g. the conversation counter). Streamlit places it on the left anyway.
    render_sidebar(settings)


main()
