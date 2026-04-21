"""
IPL Analytics Chatbot — app.py
================================
Streamlit conversational BI interface for IPL ball-by-ball analytics.
Powered by: AWS Bedrock (Claude 3.5 Haiku) + Amazon Athena + Parquet
 
ARCHITECTURE:
  User Question
    → sql_generator.py   (Bedrock: NL → SQL)
    → athena_client.py   (Execute SQL on Parquet tables)
    → answer_formatter.py (Bedrock: rows → clean answer)
    → Plotly chart (optional, auto-detected)
 
RUN LOCALLY:
  pip install streamlit plotly
  streamlit run app.py
 
DEPLOY (Streamlit Cloud):
  - Push repo to GitHub
  - Connect on share.streamlit.io
  - Add AWS credentials in App Settings → Secrets
"""
 
from __future__ import annotations
 
import os
import sys
import time
import streamlit as st
import plotly.express as px
import pandas as pd
 
# ── Path setup so src/ imports work when run from project root ───
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
 
from src.sql_generator    import generate_sql, is_out_of_scope
from src.athena_client    import run_query, format_results_as_table
from src.answer_formatter import format_answer, results_to_chart_data
 
# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
 
APP_TITLE       = "IPL Analytics Chatbot"
APP_SUBTITLE    = "Ask any cricket question — powered by 19 seasons of ball-by-ball data"
MAX_QUERIES     = 20          # Rate limit per session
ATHENA_BUCKET   = "s3://abhishek-ipl-analytics-2026/athena-results/"
 
# Inject AWS credentials from Streamlit Secrets when deployed to Streamlit Cloud.
# Locally: falls back to ~/.aws/credentials (set by `aws configure`).
try:
    if "AWS_ACCESS_KEY_ID" in st.secrets:
        os.environ["AWS_ACCESS_KEY_ID"]     = st.secrets["AWS_ACCESS_KEY_ID"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = st.secrets["AWS_SECRET_ACCESS_KEY"]
        os.environ["AWS_DEFAULT_REGION"]    = st.secrets.get("AWS_DEFAULT_REGION", "us-east-1")
        os.environ["ATHENA_OUTPUT_BUCKET"]  = st.secrets.get("ATHENA_OUTPUT_BUCKET", ATHENA_BUCKET)
except Exception:
    pass

if not os.environ.get("ATHENA_OUTPUT_BUCKET"):
    os.environ["ATHENA_OUTPUT_BUCKET"] = ATHENA_BUCKET
 
# ═══════════════════════════════════════════════════════════════════
# SUGGESTED STARTER QUESTIONS
# ═══════════════════════════════════════════════════════════════════
 
SUGGESTED_QUESTIONS = [
    "Who were the top 5 run scorers in IPL 2024?",
    "How many wickets did Jasprit Bumrah take in IPL 2024?",
    "Which team won the most matches in IPL 2023?",
    "What is Virat Kohli's total IPL run tally across all seasons?",
    "Who has the best bowling economy rate in IPL history (min 50 overs)?",
    "Which venue hosted the most IPL matches?",
    "How many sixes did MS Dhoni hit in his IPL career?",
    "Who scored the most runs for Mumbai Indians in IPL 2023?",
    "What was the highest team total in IPL 2024?",
    "Which bowler took the most wickets in IPL history?",
]
 
# ═══════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════════
 
st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🏏",
    layout="wide",
    initial_sidebar_state="expanded",
)
 
# ═══════════════════════════════════════════════════════════════════
# CUSTOM CSS — IPL-themed dark UI
# ═══════════════════════════════════════════════════════════════════
 
st.markdown("""
<style>
  /* ── Global ── */
  .stApp { background-color: #0f1117; }
 
  /* ── Header ── */
  .ipl-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-radius: 12px;
    padding: 24px 28px;
    margin-bottom: 20px;
    border: 1px solid #1e3a5f;
  }
  .ipl-header h1 {
    color: #f0c040;
    font-size: 28px;
    font-weight: 800;
    margin: 0 0 6px 0;
    letter-spacing: 0.5px;
  }
  .ipl-header p {
    color: #94a3b8;
    font-size: 13px;
    margin: 0;
  }
  .ipl-header .stats-row {
    display: flex;
    gap: 20px;
    margin-top: 14px;
    flex-wrap: wrap;
  }
  .ipl-stat {
    background: rgba(240,192,64,0.1);
    border: 1px solid rgba(240,192,64,0.2);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 11px;
    color: #f0c040;
    font-weight: 600;
  }
 
  /* ── Chat messages ── */
  .stChatMessage { border-radius: 10px; }
 
  /* ── SQL expander ── */
  .sql-box {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 12px 16px;
    font-family: 'Courier New', monospace;
    font-size: 11px;
    color: #7dd3fc;
    white-space: pre-wrap;
    margin-top: 8px;
  }
 
  /* ── Metadata row ── */
  .meta-row {
    display: flex;
    gap: 12px;
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .meta-chip {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 10px;
    color: #64748b;
  }
  .meta-chip.green { border-color: #166534; color: #4ade80; background: #052e16; }
  .meta-chip.blue  { border-color: #1e40af; color: #60a5fa; background: #0c1a3a; }
 
  /* ── Rate limit warning ── */
  .rate-warning {
    background: rgba(251,191,36,0.1);
    border: 1px solid rgba(251,191,36,0.3);
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 12px;
    color: #fbbf24;
    margin-top: 8px;
  }
 
  /* ── Sidebar ── */
  .sidebar-section {
    background: #1e293b;
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 12px;
  }
  .sidebar-section h4 {
    color: #f0c040;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    margin: 0 0 8px 0;
    text-transform: uppercase;
  }
  .suggested-q {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 11px;
    color: #94a3b8;
    margin-bottom: 5px;
    cursor: pointer;
    transition: border-color 0.2s;
  }
</style>
""", unsafe_allow_html=True)
 
# ═══════════════════════════════════════════════════════════════════
# SESSION STATE INIT
# ═══════════════════════════════════════════════════════════════════
 
if "messages" not in st.session_state:
    st.session_state.messages = []          # Chat history for display
 
if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = []  # LLM context (question + SQL answer pairs)
 
if "query_count" not in st.session_state:
    st.session_state.query_count = 0
 
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None
 
# ═══════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════
 
with st.sidebar:
    st.markdown("### 🏏 IPL Analytics")
    st.markdown("---")
 
    # Session stats
    queries_left = MAX_QUERIES - st.session_state.query_count
    st.markdown(f"""
    <div class="sidebar-section">
      <h4>Session</h4>
      <div style="display:flex;justify-content:space-between;font-size:12px;color:#94a3b8;">
        <span>Queries used</span><span style="color:#f0c040;font-weight:700;">{st.session_state.query_count} / {MAX_QUERIES}</span>
      </div>
      <div style="background:#1e293b;border-radius:4px;height:4px;margin-top:6px;">
        <div style="background:#f0c040;width:{min(st.session_state.query_count/MAX_QUERIES*100,100):.0f}%;height:4px;border-radius:4px;"></div>
      </div>
    </div>
    """, unsafe_allow_html=True)
 
    # Dataset info
    st.markdown("""
    <div class="sidebar-section">
      <h4>Dataset</h4>
      <div style="font-size:11px;color:#64748b;line-height:1.8;">
        📦 283,468 deliveries<br/>
        🏟️ 1,193 matches<br/>
        📅 2007/08 – 2025 seasons<br/>
        ⚡ Parquet on Amazon Athena
      </div>
    </div>
    """, unsafe_allow_html=True)
 
    # Suggested questions
    st.markdown("""
    <div style="font-size:11px;font-weight:700;color:#f0c040;letter-spacing:1px;
                text-transform:uppercase;margin:12px 0 8px 0;">
      💡 Try asking
    </div>
    """, unsafe_allow_html=True)
 
    for q in SUGGESTED_QUESTIONS[:6]:
        if st.button(q, key=f"sq_{q[:20]}", use_container_width=True):
            st.session_state.pending_question = q
 
    st.markdown("---")
 
    # Reset button
    if st.button("🔄 Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.conversation_history = []
        st.session_state.query_count = 0
        st.session_state.pending_question = None
        st.rerun()
 
    st.markdown("""
    <div style="font-size:10px;color:#334155;margin-top:12px;text-align:center;">
      Built with AWS Bedrock + Athena<br/>
      Claude 3.5 Haiku · Parquet · Streamlit
    </div>
    """, unsafe_allow_html=True)
 
# ═══════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════
 
st.markdown(f"""
<div class="ipl-header">
  <h1>🏏 {APP_TITLE}</h1>
  <p>{APP_SUBTITLE}</p>
  <div class="stats-row">
    <div class="ipl-stat">283K+ Deliveries</div>
    <div class="ipl-stat">19 Seasons</div>
    <div class="ipl-stat">1,193 Matches</div>
    <div class="ipl-stat">Powered by Claude 3.5 Haiku</div>
    <div class="ipl-stat">Amazon Athena + Parquet</div>
  </div>
</div>
""", unsafe_allow_html=True)
 
# ═══════════════════════════════════════════════════════════════════
# CHAT HISTORY DISPLAY
# ═══════════════════════════════════════════════════════════════════
 
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="🏏" if msg["role"] == "assistant" else "👤"):
        st.markdown(msg["content"])
 
        # Show SQL and metadata for assistant messages
        if msg["role"] == "assistant" and msg.get("sql"):
            with st.expander("🔍 View SQL query", expanded=False):
                st.markdown(f'<div class="sql-box">{msg["sql"]}</div>', unsafe_allow_html=True)
 
            meta_parts = []
            if msg.get("rows"):
                meta_parts.append(f'<span class="meta-chip green">✓ {msg["rows"]} rows returned</span>')
            if msg.get("elapsed"):
                meta_parts.append(f'<span class="meta-chip blue">⚡ {msg["elapsed"]}s</span>')
 
            if meta_parts:
                st.markdown(
                    f'<div class="meta-row">{"".join(meta_parts)}</div>',
                    unsafe_allow_html=True
                )
 
        # Render chart if present
        if msg["role"] == "assistant" and msg.get("chart"):
            chart = msg["chart"]
            try:
                df = pd.DataFrame(chart["data"])
                y_col = chart["y"]
                x_col = chart["x"]
                df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
 
                if chart["chart_type"] == "line":
                    fig = px.line(df, x=x_col, y=y_col, title=chart["title"],
                                  markers=True, color_discrete_sequence=["#f0c040"])
                else:
                    fig = px.bar(df, x=x_col, y=y_col, title=chart["title"],
                                 color_discrete_sequence=["#f0c040"])
 
                fig.update_layout(
                    plot_bgcolor="#0f1117",
                    paper_bgcolor="#1e293b",
                    font_color="#e2e8f0",
                    title_font_color="#f0c040",
                    xaxis=dict(showgrid=False, color="#64748b"),
                    yaxis=dict(gridcolor="#1e293b", color="#64748b"),
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass  # Chart rendering failure is non-fatal
 
# ═══════════════════════════════════════════════════════════════════
# QUESTION PROCESSING FUNCTION
# ═══════════════════════════════════════════════════════════════════
 
def process_question(question: str):
    """Run the full chain for one question and update session state."""
 
    # Rate limit check
    if st.session_state.query_count >= MAX_QUERIES:
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"⚠️ You've reached the session limit of {MAX_QUERIES} questions. "
                       "Click **Clear conversation** in the sidebar to start a new session.",
        })
        return
 
    # Add user message to display
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.query_count += 1
 
    with st.chat_message("assistant", avatar="🏏"):
        placeholder = st.empty()
        placeholder.markdown("⏳ Thinking...")
        t0 = time.time()
 
        # ── Out of scope check ────────────────────────────────────
        if is_out_of_scope(question):
            answer = (
                "That question appears to be outside the scope of IPL cricket data. "
                "I can answer questions about IPL matches, players, teams, seasons, "
                "batting and bowling statistics from 2007/08 through 2025.\n\n"
                "Try asking something like: *'Who scored the most runs in IPL 2024?'*"
            )
            placeholder.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
            st.session_state.conversation_history.append({"role": "user", "content": question})
            st.session_state.conversation_history.append({"role": "assistant", "content": answer})
            return
 
        sql      = None
        rows     = []
        answer   = ""
        chart    = None
        err_msg  = None
 
        # ── Step 1: Generate SQL ──────────────────────────────────
        try:
            placeholder.markdown("🧠 Generating SQL...")
            sql = generate_sql(
                question,
                conversation_history=st.session_state.conversation_history
            )
        except Exception as e:
            err_msg = (
                f"I had trouble generating a SQL query for that question.\n\n"
                f"Try rephrasing — for example, use full player names or specify a season.\n\n"
                f"*Technical detail: {str(e)[:120]}*"
            )
 
        # ── Step 2: Execute on Athena ─────────────────────────────
        if sql and not err_msg:
            try:
                placeholder.markdown("⚡ Querying Athena...")
                rows = run_query(sql)
            except Exception as e:
                err_msg = (
                    f"The SQL query ran into an error in Athena.\n\n"
                    f"*Technical detail: {str(e)[:200]}*"
                )
 
        # ── Step 3: Format answer ─────────────────────────────────
        if not err_msg:
            try:
                placeholder.markdown("📝 Formatting answer...")
                answer = format_answer(question, sql or "", rows)
                chart  = results_to_chart_data(rows)
            except Exception as e:
                answer = (
                    f"I got the data but had trouble formatting the answer.\n\n"
                    f"*Technical detail: {str(e)[:120]}*"
                )
 
        elapsed = round(time.time() - t0, 1)
        final_content = err_msg if err_msg else answer
        placeholder.markdown(final_content)
 
        # ── Show SQL expander ─────────────────────────────────────
        if sql:
            with st.expander("🔍 View SQL query", expanded=False):
                st.markdown(f'<div class="sql-box">{sql}</div>', unsafe_allow_html=True)
 
        # ── Metadata chips ────────────────────────────────────────
        meta_parts = []
        if rows:
            meta_parts.append(f'<span class="meta-chip green">✓ {len(rows)} rows returned</span>')
        meta_parts.append(f'<span class="meta-chip blue">⚡ {elapsed}s</span>')
        st.markdown(
            f'<div class="meta-row">{"".join(meta_parts)}</div>',
            unsafe_allow_html=True
        )
 
        # ── Render chart ──────────────────────────────────────────
        if chart:
            try:
                df = pd.DataFrame(chart["data"])
                y_col = chart["y"]
                x_col = chart["x"]
                df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
 
                if chart["chart_type"] == "line":
                    fig = px.line(df, x=x_col, y=y_col, title=chart["title"],
                                  markers=True, color_discrete_sequence=["#f0c040"])
                else:
                    fig = px.bar(df, x=x_col, y=y_col, title=chart["title"],
                                 color_discrete_sequence=["#f0c040"])
 
                fig.update_layout(
                    plot_bgcolor="#0f1117",
                    paper_bgcolor="#1e293b",
                    font_color="#e2e8f0",
                    title_font_color="#f0c040",
                    xaxis=dict(showgrid=False, color="#64748b"),
                    yaxis=dict(gridcolor="#1e293b", color="#64748b"),
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass
 
    # ── Save to session state ─────────────────────────────────────
    st.session_state.messages.append({
        "role"   : "assistant",
        "content": final_content,
        "sql"    : sql,
        "rows"   : len(rows) if rows else 0,
        "elapsed": elapsed,
        "chart"  : chart,
    })
 
    # Update LLM conversation history (trimmed to last 6 turns = 3 Q&A pairs)
    st.session_state.conversation_history.append({"role": "user",      "content": question})
    st.session_state.conversation_history.append({"role": "assistant", "content": answer[:400]})
    if len(st.session_state.conversation_history) > 12:
        st.session_state.conversation_history = st.session_state.conversation_history[-12:]
 
 
# ═══════════════════════════════════════════════════════════════════
# CHAT INPUT + SUGGESTED QUESTION HANDLER
# ═══════════════════════════════════════════════════════════════════
 
# Handle sidebar suggested question click
if st.session_state.pending_question:
    q = st.session_state.pending_question
    st.session_state.pending_question = None
    with st.chat_message("user", avatar="👤"):
        st.markdown(q)
    process_question(q)
    st.rerun()
 
# Handle typed question
if prompt := st.chat_input("Ask an IPL question... e.g. 'Who took the most wickets in IPL 2024?'"):
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)
    process_question(prompt)
 
# ── Empty state — shown before first message ──────────────────────
if not st.session_state.messages:
    st.markdown("""
    <div style="text-align:center;padding:40px 20px;color:#475569;">
      <div style="font-size:48px;margin-bottom:16px;">🏏</div>
      <div style="font-size:18px;font-weight:600;color:#94a3b8;margin-bottom:8px;">
        Ask any IPL cricket question
      </div>
      <div style="font-size:13px;color:#475569;">
        Player stats · Team performance · Season records · Head-to-head · Venue analysis
      </div>
      <div style="margin-top:20px;font-size:12px;color:#334155;">
        Try a suggested question from the sidebar →
      </div>
    </div>
    """, unsafe_allow_html=True)
 