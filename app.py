"""
app.py - GenAI SQL Assistant (Streamlit web interface)

Start with:   streamlit run app.py

Pipeline (Phase 6):
    question -> local LLM writes SQL -> SQL VALIDATOR -> read-only login -> SQL Server -> results

Phase history:
  Phase 3  page layout
  Phase 4  Ollama status in the sidebar
  Phase 5  the local LLM generates the SQL
  Phase 6  SQL validator checks every query before it runs   <- this version
  Phase 7  AI explanation of the results
"""

import streamlit as st

import schema
from config import ConfigError, get_ollama_settings, get_settings
from database import DatabaseError, check_permissions, run_query, test_connection
from ollama_client import get_status as get_ollama_status
from sql_generator import EXAMPLE_QUESTIONS, SQLGenerationError, generate_sql
from sql_validator import validate_sql

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
    outcome = {"question": question, "sql": sql, "generation": None, "validation": None,
               "notice": None, "result": None, "error": None, "error_stage": None}
    st.session_state.outcome = outcome

    # Step 1: question -> SQL with the local LLM (skipped for hand-typed SQL)
    if sql is None:
        try:
            generation = generate_sql(question)
        except SQLGenerationError as exc:
            outcome["error"], outcome["error_stage"] = str(exc), "SQL generation"
            return
        outcome["generation"] = vars(generation)
        if generation.status != "ok":            # clarify / unanswerable
            outcome["notice"] = (generation.status, generation.message)
            return
        outcome["sql"] = generation.sql

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

        if st.button("🔄 Refresh status", width="stretch"):
            st.cache_data.clear()
            schema.clear_cache()           # re-read tables and columns too
            st.rerun()


# ---------------------------------------------------------------------------
# Results area
# ---------------------------------------------------------------------------

def render_outcome() -> None:
    outcome = st.session_state.get("outcome")
    if outcome is None:
        return

    st.divider()
    if outcome["question"]:
        st.markdown(f"**Question:** {outcome['question']}")

    generation = outcome["generation"]
    if generation:
        st.caption(f"🤖 {generation['model']} answered in {generation['elapsed_seconds']:.1f} s "
                   f"({generation['prompt_tokens']:,} prompt tokens + "
                   f"{generation['output_tokens']:,} output tokens)")

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

    # AI explanation (Phase 7)
    st.subheader("AI explanation")
    st.info("The local LLM will explain these results in plain English in Phase 7.")

    render_behind_the_scenes(generation)


def render_behind_the_scenes(generation: dict | None) -> None:
    """Show exactly what was sent to and received from the LLM (for teaching)."""
    if not generation:
        return
    with st.expander("🔍 Behind the scenes: what the LLM saw and said"):
        st.markdown("**System prompt** (rules + live schema, sent with every question):")
        st.code(generation["system_prompt"], language="text")
        st.markdown("**Raw model output** (structured JSON):")
        st.code(generation["raw_output"], language="json")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        settings = get_settings()
    except ConfigError as exc:
        st.error(f"Configuration problem: {exc}")
        st.stop()

    render_sidebar(settings)

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


main()
