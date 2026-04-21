# 🏏 IPL Analytics Chatbot

A production-grade conversational analytics platform for Indian Premier League (IPL) cricket data. Ask any question in plain English and get data-backed answers powered by a Text-to-SQL AI pipeline built on AWS.

**[🚀 Live Demo](https://ipl-analytics-chatbot.streamlit.app)** · **[📊 Data: Cricsheet IPL 2007–2025](https://cricsheet.org/)**

---

## What It Does

Type a question like *"Who took the most wickets in IPL 2024?"* and the app:
1. Sends your question to Claude 3.5 Haiku (via AWS Bedrock)
2. Claude generates an Athena SQL query from the schema context
3. Athena runs the query against 283K+ ball-by-ball deliveries stored as Parquet on S3
4. Claude formats the result into a natural language answer with context
5. Plotly renders a chart if the data is multi-row

Multi-turn conversation is supported — ask follow-up questions and the context carries forward.

---

## Architecture

```
User Question
     │
     ▼
┌─────────────────┐     ┌──────────────────────┐
│  Streamlit UI   │────▶│  AWS Bedrock          │
│  (app.py)       │     │  Claude 3.5 Haiku     │
└─────────────────┘     │  Text-to-SQL Generator│
                        └──────────┬───────────┘
                                   │ SQL
                                   ▼
                        ┌──────────────────────┐
                        │  Amazon Athena        │
                        │  (Serverless SQL)     │
                        └──────────┬───────────┘
                                   │ Results
                                   ▼
                        ┌──────────────────────┐
                        │  AWS S3              │
                        │  Parquet Dataset     │
                        │  283K+ deliveries    │
                        └──────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │  AWS Bedrock          │
                        │  Answer Formatter     │
                        │  + Plotly Chart       │
                        └──────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Streamlit (Python) |
| **AI / LLM** | AWS Bedrock — Claude 3.5 Haiku |
| **Query Engine** | Amazon Athena (serverless SQL) |
| **Storage** | Amazon S3 + Apache Parquet |
| **Data Catalog** | AWS Glue |
| **Charts** | Plotly |
| **Deployment** | Streamlit Community Cloud |

---

## Dataset

- **Source:** [Cricsheet IPL ball-by-ball data](https://cricsheet.org/) (open dataset)
- **Coverage:** 19 IPL seasons — 2007/08 through 2025
- **Size:** 283,468 deliveries across 1,193 matches
- **Tables:** `deliveries_pq` (ball-level) + `matches_pq` (match-level)
- **Format:** Parquet on S3, queried via Athena — sub-10s response times

---

## Sample Questions

- *"How many wickets did Jasprit Bumrah take in his IPL career?"*
- *"Who are the top 5 run-scorers of IPL 2024?"*
- *"Which team has won the most matches at Wankhede Stadium?"*
- *"How many times has CSK beaten MI in IPL history?"*
- *"What is Virat Kohli's strike rate in the last 3 seasons?"*
- *"Show me the top 5 teams by total wins — with a chart"*

---

## Project Structure

```
ipl-analytics-chatbot/
├── app.py                        # Streamlit chatbot UI
├── src/
│   ├── sql_generator.py          # Text-to-SQL via Bedrock + player name resolution
│   ├── athena_client.py          # Athena query execution + polling
│   ├── bedrock_client.py         # Bedrock Runtime wrapper
│   └── answer_formatter.py       # Natural language answer formatting
├── prompts/
│   └── schema_context.txt        # Full schema + SQL rules injected as system prompt
├── scripts/                      # Data pipeline scripts (ETL, S3 upload, DQ tests)
├── requirements.txt
└── runtime.txt                   # Python 3.11
```

---

## Running Locally

**Prerequisites:** Python 3.10+, AWS credentials configured (`aws configure`)

```bash
git clone https://github.com/avernekar272/ipl-analytics-chatbot.git
cd ipl-analytics-chatbot
pip install -r requirements.txt
streamlit run app.py
```

Set these environment variables (or create `.streamlit/secrets.toml`):

```toml
AWS_ACCESS_KEY_ID = "your-key"
AWS_SECRET_ACCESS_KEY = "your-secret"
AWS_DEFAULT_REGION = "us-east-1"
ATHENA_OUTPUT_BUCKET = "s3://your-bucket/athena-results/"
```

> **Note:** You need your own S3 bucket, Athena database, and Bedrock model access to run this locally. The live demo at [ipl-analytics-chatbot.streamlit.app](https://ipl-analytics-chatbot.streamlit.app) is fully functional without any setup.

---

## Key Engineering Decisions

**Player name resolution** — Cricsheet uses two naming formats across seasons (`RG Sharma` pre-2020, `Rohit Sharma` post-2020). A self-resolving `player_names` CTE finds all name variants before the main query runs, ensuring cross-season accuracy.

**Winner inference** — No explicit "winner" column exists in ball-by-ball data. Match winners are inferred by comparing innings totals (team with higher innings-2 score wins), covering ~95% of matches correctly.

**Anti-hallucination guardrails** — The answer formatter is explicitly instructed to only use numbers from the Athena result table, ignore user-stated numbers, and flag contradictions — preventing the LLM from blending training memory with query results.

**Schema-as-system-prompt** — The full database schema, SQL rules, player aliases, and venue mappings are injected as Claude's system prompt with hot-reload on file change (no restart needed for prompt tuning).

---

## Built By

**Abhishek Vernekar** — Senior Consultant, Deloitte US  
Data Strategy · ETL QA · AI Engineering  
[LinkedIn](https://linkedin.com/in/abhishekvernekar) · [GitHub](https://github.com/avernekar272)
