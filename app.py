"""
app.py - GenAI SQL Assistant (Streamlit web interface)

Start with:   streamlit run app.py

PHASE 3: the complete page layout, without AI.
  question -> sql_generator (placeholder) -> database -> results table
PHASE 4: the sidebar shows the local LLM (Ollama) status.

Later phases plug into the empty places:
  Phase 5  sql_generator uses the local LLM
  Phase 6  SQL validator between generation and execution
  Phase 7  AI explanation of the results
"""

import streamlit as st

from config import ConfigError, get_ollama_settings, get_settings
from database import DatabaseError, check_permissions, run_query, test_connection
from ollama_client import get_status as get_ollama_status
from sql_generator import EXAMPLE_QUESTIONS, SQLGenerationError, generate_sql

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
# The pipeline: question -> SQL -> results
# The outcome is stored in st.session_state so it survives re-runs
# (for example when the sidebar "Refresh" button is clicked).
# ---------------------------------------------------------------------------

def run_pipeline(question: str | None, sql: str | None = None) -> None:
    outcome = {"question": question, "sql": sql, "result": None,
               "error": None, "error_stage": None}
    st.session_state.outcome = outcome

    # Step 1: question -> SQL (skipped when the user typed SQL manually)
    if sql is None:
        try:
            outcome["sql"] = generate_sql(question)
        except SQLGenerationError as exc:
            outcome["error"], outcome["error_stage"] = str(exc), "SQL generation"
            return

    # Step 2: SQL -> database -> DataFrame
    try:
        outcome["result"] = run_query(outcome["sql"])
    except DatabaseError as exc:
        outcome["error"], outcome["error_stage"] = str(exc), "SQL execution"


def use_example(question: str) -> None:
    """Button callback: put the example in the input box AND ask it.
    Callbacks run before the page is redrawn, so the results appear
    straight away."""
    st.session_state.question = question
    run_pipeline(question)


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
        st.caption(f"Row limit: {settings.max_rows:,} · Query timeout: {settings.query_timeout}s")

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

    # Generated SQL
    st.subheader("Generated SQL")
    if outcome["sql"]:
        st.code(outcome["sql"], language="sql")
    else:
        st.caption("No SQL was produced.")

    # Execution status
    if outcome["error"]:
        st.error(f"**{outcome['error_stage']} failed:** {outcome['error']}")
        return

    result = outcome["result"]
    st.success("Query executed successfully")

    col1, col2, col3 = st.columns(3)
    col1.metric("Rows returned", f"{result.row_count:,}")
    col2.metric("Execution time", f"{result.elapsed_seconds:.3f} s")
    col3.metric("Columns", len(result.dataframe.columns))

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

    if asked:
        if not question.strip():
            st.warning("Please type a question first.")
        else:
            with st.spinner("Working..."):
                run_pipeline(question.strip())

    # Results appear directly under the question box.
    render_outcome()

    # Example questions
    with st.expander("💡 Example questions (click one to ask it; only these work in Phase 3)"):
        cols = st.columns(2)
        for i, example in enumerate(EXAMPLE_QUESTIONS):
            cols[i % 2].button(example, key=f"example_{i}", width="stretch",
                               on_click=use_example, args=(example,))

    # Developer tool: run SQL typed by hand
    with st.expander("🛠️ Developer: run your own SQL"):
        st.caption("Useful for testing. Until the SQL validator exists (Phase 6), "
                   "only the database permissions protect us, so this is "
                   "allowed ONLY with a read-only login.")
        manual_sql = st.text_area("SQL", height=120, key="manual_sql",
                                  placeholder="SELECT TOP 5 * FROM dbo.Products;")
        if st.button("Run SQL"):
            if get_read_only_status() is not True:
                st.error("Blocked: the database login is not confirmed read-only.")
            elif not manual_sql.strip():
                st.warning("Please type some SQL first.")
            else:
                with st.spinner("Running..."):
                    run_pipeline(None, sql=manual_sql.strip())
                st.rerun()   # redraw so the results appear under the question box


main()
