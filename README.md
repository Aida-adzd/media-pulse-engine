# Media Pulse Engine

A personal knowledge and media intelligence pipeline that turns anything you save — YouTube videos, articles, Instagram posts — into a searchable, connected knowledge base with daily AI-generated digests.

Send a URL to a Telegram bot. Everything else is automatic.

---

## How It Works

```
Telegram Bot
    │  send URL
    ▼
n8n (orchestrator)
    │  classify & store
    ▼
Supabase (sources table, status = pending)
    │
    │  scheduled poll every minute
    ▼
n8n picks up pending row → claims it (status = fetching)
    │
    ▼
Extraction Service (FastAPI)
    ├── /extract   → fetch raw content (YouTube transcript / article text / Instagram caption)
    ├── /process   → Gemini summarization + chunking + embedding + Supabase writes
    ├── /link      → pgvector similarity search → Gemini relationship classification
    └── /digest    → daily synthesis of all completed sources → Telegram notification
    │
    ▼
Supabase / pgvector
    ├── sources      (metadata + summary, status = completed)
    ├── chunks       (1024-dim embeddings via intfloat/e5-large-v2)
    ├── insights     (key points, action items, entities, questions)
    ├── tags         (topic keywords)
    ├── connections  (semantic links between sources)
    └── digests      (daily markdown syntheses)
```

---

## Features

- **Multi-source ingestion** — YouTube (transcript via `youtube-transcript-api` + `yt-dlp` fallback), web articles (trafilatura), Instagram captions (yt-dlp)
- **AI summarization** — Gemini 2.5 Flash Lite extracts summaries, key points, action items, entities, questions, and topic tags per source
- **Semantic search** — `/search <query>` in Telegram returns a Gemini-synthesized answer grounded in your saved content via pgvector cosine similarity
- **Knowledge linking** — every new source is automatically compared against existing content; meaningful relationships (related / builds_on / contradicts / duplicate) are stored
- **Daily digests** — a scheduled workflow synthesizes all sources processed in the last 24 hours into a structured markdown digest sent to Telegram
- **Idempotent processing** — status-based row locking (`pending → fetching → processing → completed | failed`) prevents double-processing if scheduled runs overlap
- **Graceful degradation** — linking failures never re-mark completed sources as failed; the pipeline keeps moving

---

## Stack

| Layer | Technology |
|---|---|
| Ingestion | Telegram Bot API + ngrok (webhook) |
| Orchestration | n8n (self-hosted, no Code nodes) |
| Intelligence | FastAPI microservice (Python 3.11) |
| LLM | Google Gemini 2.5 Flash Lite |
| Embeddings | intfloat/e5-large-v2 (1024-dim, CPU, via sentence-transformers) |
| Storage | Supabase (PostgreSQL + pgvector) |
| Content fetching | trafilatura, youtube-transcript-api, yt-dlp |
| Infrastructure | Docker Compose |

---

## Project Structure

```
media-pulse-engine/
├── docker-compose.yml              # All services: n8n, extraction, ngrok
├── .env.example                    # Environment variable template
│
├── services/
│   └── extraction/                 # FastAPI intelligence microservice
│       ├── app/
│       │   ├── main.py             # Route definitions
│       │   ├── schemas.py          # Pydantic request/response models
│       │   ├── db.py               # Supabase client helpers
│       │   ├── gemini.py           # Gemini API call + retry logic
│       │   ├── embeddings.py       # e5-large-v2 embedding functions
│       │   ├── processor.py        # /process — summarize + chunk + embed + write
│       │   ├── linker.py           # /link — similarity search + relationship classification
│       │   ├── synthesizer.py      # /digest — period synthesis
│       │   ├── searcher.py         # /search — semantic search + answer synthesis
│       │   └── extractors/
│       │       ├── youtube.py      # Transcript extraction
│       │       ├── article.py      # Web article extraction
│       │       └── instagram.py    # Instagram caption extraction
│       ├── Dockerfile
│       └── requirements.txt
│
├── n8n/
│   └── Dockerfile                  # n8n base image
│
├── workflows/
│   ├── phase0_ingestion.json       # Telegram webhook → Supabase insert
│   ├── phase3_processing.json      # Poll pending → extract → process → link
│   ├── phase3_digest.json          # Daily digest → Telegram notify
│   └── phase_retry.json            # Re-queue stale failed sources
│
├── supabase/
│   ├── schema.sql                  # Full database schema
│   └── migrations/                 # Incremental schema changes
│
└── scripts/
    └── backfill_tags.py            # One-off: tag sources that pre-date auto-tagging
```

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- A [Supabase](https://supabase.com) project with the schema applied (see `supabase/schema.sql`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A [Google Gemini API key](https://aistudio.google.com/app/apikey)
- An [ngrok](https://ngrok.com) account and auth token (free tier works)

### 1. Configure environment

```bash
cp .env.example .env
```

Fill in `.env`:

```env
NGROK_AUTHTOKEN=your_ngrok_token
WEBHOOK_URL=https://your-domain.ngrok-free.app

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key

GEMINI_API_KEY=your_gemini_key

EXTRACTION_SERVICE_URL=http://extraction:8000
```

### 2. Apply the database schema

Run `supabase/schema.sql` against your Supabase project via the SQL editor or CLI.

### 3. Start the stack

```bash
docker compose up -d --build
```

Services:
| Service | URL |
|---|---|
| n8n | http://localhost:5678 |
| Extraction API | http://localhost:8001 |
| ngrok inspector | http://localhost:4040 |

### 4. Import n8n workflows

In n8n (http://localhost:5678):

1. Import `workflows/phase0_ingestion.json` — handles Telegram webhook
2. Import `workflows/phase3_processing.json` — processes pending sources
3. Import `workflows/phase3_digest.json` — runs daily digest at 9am
4. Set your Telegram credential in the Telegram nodes
5. Activate all three workflows

### 5. Register the Telegram webhook

Get your ngrok URL from http://localhost:4040, then:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<NGROK_URL>/webhook/<PATH>
```

The webhook path is shown in the n8n Trigger node settings.

### 6. Send a URL

Message your bot with any YouTube, article, or Instagram URL. You'll get a confirmation reply immediately and a processing notification when complete.

---

## Extraction Service API

The extraction service runs at `http://localhost:8001` (externally) and `http://extraction:8000` (from within Docker).

### Content Fetching

```
POST /extract
{"url": "https://youtu.be/...", "content_type": "youtube"}
```

Supported `content_type` values: `youtube`, `article`, `instagram`

Returns: `{title, author, raw_content, metadata}`

### Processing Pipeline

```
POST /process
{"source_id": "uuid"}
```

Reads `raw_content` from Supabase, calls Gemini for summarization + insights + tags, chunks the content, embeds chunks with e5-large-v2, writes everything back to Supabase, and marks the source `completed`.

### Semantic Linking

```
POST /link
{"source_id": "uuid"}
```

Embeds the source summary, finds similar existing sources via pgvector, classifies relationships with Gemini. Always returns 200 — linking failures degrade gracefully.

### Digest Synthesis

```
POST /digest
{"period_type": "daily", "since": "2026-06-19T00:00:00Z", "until": "2026-06-20T00:00:00Z"}
```

Synthesizes all completed sources in the window into a structured markdown digest with Key Themes, Top Insights, Action Items, and Open Questions.

### Semantic Search

```
POST /search
{"query": "how do transformers work", "match_count": 5}
```

Embeds the query, finds the most relevant chunks via pgvector, and returns a Gemini-synthesized answer grounded in your saved content.

### Embeddings

```
POST /embed        {"text": "...", "mode": "query"}    # single
POST /embed-batch  {"texts": [...], "mode": "passage"} # batch
```

Returns 1024-dimensional e5-large-v2 embeddings. Use `mode: "query"` for search queries and `mode: "passage"` for document chunks.

---

## Data Model

```sql
sources      -- one row per saved URL; status: pending → fetching → processing → completed | failed
chunks       -- text chunks with vector(1024) embeddings (HNSW cosine index)
insights     -- key_point | action_item | entity | question extracted per source
tags         -- topic keywords; many-to-many via source_tags
connections  -- semantic links between sources with relationship_type and similarity_score
digests      -- periodic syntheses with source_ids[] for traceability
```

The `match_chunks` RPC performs pgvector cosine similarity search with optional `content_type` filtering.

---

## Telegram Commands

| Command | Description |
|---|---|
| Send a URL | Ingests the content into the pipeline |
| `/search <query>` | Semantic search across your knowledge base |

---

## Configuration Reference

| Variable | Required | Description |
|---|---|---|
| `NGROK_AUTHTOKEN` | Yes | ngrok authentication token |
| `WEBHOOK_URL` | Yes | Public URL ngrok exposes (e.g. `https://xyz.ngrok-free.app`) |
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | Your Telegram chat/user ID (whitelist filter) |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Supabase service role key (bypasses RLS — keep secret) |
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `EXTRACTION_SERVICE_URL` | Yes | Internal service URL (`http://extraction:8000`) |
| `GEMINI_MODEL` | No | Override Gemini model (default: `gemini-2.5-flash-lite`) |

> **Security note:** The `SUPABASE_SERVICE_ROLE_KEY` bypasses Row Level Security and acts as a full database admin password. Never commit it or expose it publicly. If compromised, rotate it immediately from the Supabase dashboard → Project Settings → API.

---

## Architecture Decisions

**n8n as pure orchestrator** — n8n handles triggers, scheduling, and HTTP calls only. No scraping, parsing, or AI logic lives in n8n Code nodes. This boundary exists because n8n's sandboxed runtime cannot run `yt-dlp`, `trafilatura`, or Python ML libraries.

**Single intelligence microservice** — all content understanding (fetching, summarizing, embedding, linking, digesting) is consolidated in one FastAPI service. No separate microservices, no agentic frameworks. Each operation is a sequential Python function.

**Status-based idempotency** — rows are claimed (`status = fetching`) before any work begins, acting as an optimistic lock. Overlapping scheduled runs cannot double-process the same source.

**Graceful degradation** — the `/link` endpoint never fails n8n. Relationship linking is an enhancement; a Gemini timeout or rate limit returns `connections_written: 0` rather than marking a completed source as failed.

**Local embeddings** — intfloat/e5-large-v2 runs in the extraction container (CPU-only PyTorch). No external embedding API, no per-token cost, no latency dependency.


