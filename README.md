# DWERP-Agent: Complete Architecture & Implementation Guide

> Written for developers inheriting or extending this codebase. Covers every component — LLMs, RAG, memory, SQL execution, security, caching, and deployment.

---

## Table of Contents

1. [What This System Is](#1-what-this-system-is)
2. [High-Level Flow](#2-high-level-flow)
3. [Technology Stack](#3-technology-stack)
4. [LLM Integration & Routing](#4-llm-integration--routing)
5. [System Prompt Assembly](#5-system-prompt-assembly)
6. [RAG — Document Search](#6-rag--document-search)
7. [Schema RAG — Smart Schema Selection](#7-schema-rag--smart-schema-selection)
8. [3-Tier Memory System](#8-3-tier-memory-system)
9. [Personal Memory & Onboarding](#9-personal-memory--onboarding)
10. [SQL Tool & Query Execution](#10-sql-tool--query-execution)
11. [Chart & Visualization Detection](#11-chart--visualization-detection)
12. [API Endpoints & Streaming](#12-api-endpoints--streaming)
13. [Authentication & Multi-Tenancy](#13-authentication--multi-tenancy)
14. [Caching Strategy](#14-caching-strategy)
15. [DWERP CRM Database Schema](#15-dwerp-crm-database-schema)
16. [Deployment (Docker + Jenkins)](#16-deployment-docker--jenkins)
17. [Database Migrations](#17-database-migrations)
18. [End-to-End Query Walkthroughs](#18-end-to-end-query-walkthroughs)
19. [Monitoring & Debugging](#19-monitoring--debugging)
20. [Known Limitations & Future Work](#20-known-limitations--future-work)
21. [Key Files Quick Reference](#21-key-files-quick-reference)

---

## 1. What This System Is

**MSBC Agent** is an AI-powered data assistant for the **DWERP CRM system** used by a fenestration manufacturing company (aluminium windows, UPVC doors, glass systems, curtain walls, etc.).

Users type plain-English questions. The agent:
- Queries the CRM's PostgreSQL database
- Searches uploaded documents (specs, manuals, policies)
- Returns answers as text, KPI cards, charts, and tables
- Learns from every successful and failed query

It is **multi-tenant** — multiple companies share the same deployment, with strict data isolation enforced at every layer.

---

## 2. High-Level Flow

```
User types a question
        │
        ▼
[FastAPI /api/chat]  ──── SSE streaming response back to browser
        │
        ▼
[JWT Authentication]  ─── Extracts tenant_id, user_id
        │
        ▼
[Prompt Injection Check]  ─── Block "ignore previous instructions" etc.
        │
        ▼
[Fast-Path Conversational Check]  ─── "hi / thanks / how are you"
        │                               → Grok 4.1 Fast, <100ms, skip all RAG
        ▼
[Cache Check]  ─── Exact match + Semantic match (Redis)
        │               → Cache hit: return immediately
        ▼
[Personal Memory Load]  ─── User name, preferred format, agent name
        │
        ▼
[System Prompt Assembly]
        ├── Persona template (professional / smart / casual)
        ├── Schema RAG  ─── Embed question → find top-10 relevant tables
        ├── Few-shot examples (hardcoded + from Tier-1 query memory)
        ├── Error patterns (Tier-2 memory — "DON'T do this")
        └── Learned facts (Tier-3 knowledge)
        │
        ▼
[LangGraph ReAct Agent Loop]  ─── max 5 iterations
        ├── LLM decides: call a tool or give final answer
        ├── Tools available:
        │     ├── dwerp_sql_query  ─── generates + runs SQL against CRM DB
        │     ├── search_documents ─── hybrid vector + BM25 search on uploads
        │     ├── search_agent_memory ─── recall past queries
        │     └── propose_action  ─── (future) write operations
        └── Repeats until answer complete or max iterations reached
        │
        ▼
[Response Parsing]
        ├── Detect: single row → KPI cards
        ├── Detect: multi-row + category col → bar/pie chart
        ├── Detect: time column → line chart
        └── Emit: text + chart(s) + table + KPI as SSE chunks
        │
        ▼
[Post-processing]
        ├── Store Q→SQL in query memory (Tier 1)
        ├── Store errors in error memory (Tier 2)
        ├── Cache response (exact + semantic)
        └── Write audit log (model, latency, tokens, user, tenant)
```

---

## 3. Technology Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend framework | FastAPI 0.115+ | Async, SSE streaming |
| Agent framework | LangGraph 0.4+ | ReAct loop, thread checkpointing |
| LLM orchestration | OpenRouter API | Routes to Anthropic / Google / xAI |
| Primary LLM | Claude Haiku 4.5 | `anthropic/claude-haiku-4-5` |
| Embeddings | Google Gemini Embedding 2 | 768-dimensional vectors |
| Reranker | `BAAI/bge-reranker-v2-m3` | Cross-encoder, loaded locally |
| Vector store | pgvector (PostgreSQL 16) | No separate Pinecone/Weaviate |
| Full-text search | PostgreSQL `ts_rank` / `tsquery` | BM25-style |
| Cache | Redis 7 | Exact + semantic cache |
| Database (async) | asyncpg | Connection pooling (10–20 conns) |
| Conversation store | PostgreSQL | LangGraph async checkpointer |
| Auth | JWT (HS256) | Own JWT + Supabase JWT support |
| Frontend | Next.js 15, React 19, Recharts | SSE consumer, chart rendering |
| Deployment | Docker, Jenkins, Nexus registry | Port 8001 |
| Local LLM fallback | Ollama (DeepSeek R1 32B) | Only if OpenRouter is down |

---

## 4. LLM Integration & Routing

### 4.1 Current Configuration (Default: FORCE_TIER1)

As of the W7e 2026-05-12 performance fix, **`FORCE_TIER1 = True`** is hardcoded in `router.py`.

This means **every query goes to Claude Haiku 4.5** via OpenRouter, regardless of complexity. Multi-tier routing is disabled because Haiku is fast enough (1–3s per ReAct iteration vs. 5–8s with routing overhead).

To re-enable multi-tier routing:
```bash
MSBC_AGENT_MULTI_TIER=true  # in agent.env
```

### 4.2 Three-Tier Model Routing (When Multi-Tier Enabled)

| Tier | Model | Use Case | Keywords That Trigger It |
|------|-------|----------|-------------------------|
| Tier 1 | `anthropic/claude-haiku-4-5` | Complex analysis | analyze, forecast, recommend, why, explain, health |
| Tier 2 | `google/gemini-3.1-flash-lite-preview` | Medium — joins, trends | by, breakdown, top, ranking, funnel, compare |
| Tier 3 | `x-ai/grok-4.1-fast` | Simple lookups | how many, count, list, show |

**Conversational fast-path** (bypasses all tiers):
- Model: Grok 4.1 Fast
- Triggers: "hi", "hello", "thanks", "how are you", etc.
- Max 20 words response, no RAG, <100ms

### 4.3 Fallback Chain

If OpenRouter is unreachable:
1. Direct Anthropic API (`ANTHROPIC_API_KEY`) → Claude Haiku
2. Local Ollama (`OLLAMA_BASE_URL`) → DeepSeek R1 32B or `qwen2.5:7b`

### 4.4 LLM Configuration (agent.env)

```bash
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
ANTHROPIC_API_KEY=sk-ant-...          # Direct fallback
GOOGLE_API_KEY=...                     # Embeddings only
TIER1_MODEL=anthropic/claude-haiku-4-5
TIER2_MODEL=google/gemini-3.1-flash-lite-preview
TIER3_MODEL=x-ai/grok-4.1-fast
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=deepseek-r1:32b
```

---

## 5. System Prompt Assembly

Every query assembles a fresh system prompt from multiple sources. File: `backend/app/agent/prompts.py` → `build_system_prompt()`.

### 5.1 Components (in injection order)

#### 1. Persona Template
Three tones, selected per user or defaulting to "smart":

| Persona | Style |
|---------|-------|
| `professional` | Data-first, formal, numbers upfront |
| `smart` | Analytical, surface trends and risks, add recommendations |
| `casual` | Friendly, approachable, emojis OK |

#### 2. Domain Context (hardcoded)
From `backend/app/connectors/dwerp.py` — always included:
- Company description (fenestration manufacturer)
- Product terminology (aluminium profiles, DGU/TGU glass, curtain walls, etc.)
- Business synonyms (enquiry = lead, grand_total = quotation value)
- Common column-name mistakes

#### 3. Schema RAG (dynamic, ~20 tables → top 10)
See [Section 7](#7-schema-rag--smart-schema-selection).

#### 4. Hardcoded Few-Shot Examples
`backend/app/connectors/dwerp_templates.py` — 30+ static NL→SQL pairs.

#### 5. Dynamic Few-Shot from Memory (Tier 1)
Top 3 most similar past successful queries (via pgvector similarity, threshold 0.85).

#### 6. Error Patterns (Tier 2)
Recent 5 failed SQL patterns, injected as "DO NOT repeat these".

#### 7. Learned Facts (Tier 3)
All connector_knowledge facts scoped to this tenant.

#### 8. Personal Context
User display name, preferred format, agent name, topics of interest.

#### 9. Tool Selection Rules
Explicit guidance: when to use `dwerp_sql_query` vs. `search_documents` vs. `search_agent_memory`.

#### 10. Response Format Rules
- 1-sentence headline (max 25 words)
- 2–3 KPI cards for single-row results
- 1 primary chart (never 2+ of same dimension)
- 2–3 follow-up suggestions
- No advisory prose, no "would you like"
- Currency: ₹ for Indian Rupees, format large numbers (₹73M)

---

## 6. RAG — Document Search

Users can upload PDFs, DOCX, and XLSX files. The agent searches them alongside CRM data.

### 6.1 Upload & Ingestion Pipeline

**Endpoint**: `POST /api/upload`

```
File upload
    │
    ▼
Text extraction
    ├── PDF  → pdfplumber
    ├── DOCX → python-docx
    └── XLSX → openpyxl
    │
    ▼
Chunking  (backend/app/rag/chunker.py)
    ├── Recursive Character Splitter: 2000 chars, 400 char overlap
    ├── Quality filter: skip chunks <50 chars, <10 words, <30% alpha
    ├── Garbage removal: page numbers, headers/footers, copyright lines
    └── Table protection: keep table rows together
    │
    ▼
Deduplication
    └── MD5 hash of first 200 chars → skip near-identical chunks
    │
    ▼
Embedding  (backend/app/rag/embedder.py)
    └── Google Gemini Embedding 2 → 768-dim vectors
        Batched: 50 chunks per API call
    │
    ▼
Storage → document_chunks table (PostgreSQL + pgvector)
```

**Database tables**:
```sql
uploaded_documents:
  id, filename, file_type, description, category, 
  uploaded_by, tenant_id, chunk_count, file_size_bytes, created_at

document_chunks:
  id, document_id, chunk_index, content (text),
  embedding (vector(768)),
  metadata (jsonb)  -- page_number, language, word_count
  content_tsv       -- auto-generated full-text search index
```

### 6.2 Hybrid Search Pipeline

File: `backend/app/rag/search.py`

```
User question
    │
    ├──[1] Dense Search (pgvector)
    │       Embed query → cosine similarity on document_chunks.embedding
    │       Top 20 candidates
    │
    ├──[2] Sparse Search (PostgreSQL Full-Text / BM25-style)
    │       plainto_tsquery() on content_tsv
    │       ts_rank() scoring → top 20 candidates
    │
    ├──[3] Quick Facts Search
    │       Keyword extraction from query (stop words removed)
    │       Keyword match on connector_knowledge.fact_key + fact_value
    │       Score: 0.8 static → merged with doc results
    │
    ▼
[4] Reciprocal Rank Fusion (RRF)
    │   alpha = 0.7 (dense weight) + 0.3 (sparse weight)
    │   score = alpha/(rank+60) + (1-alpha)/(rank+60)
    │   → Top 20 combined candidates
    │
    ▼
[5] BGE Reranker (BAAI/bge-reranker-v2-m3)
    │   Cross-encoder fine-grained scoring
    │   Top 20 → Top 5 final results
    │   Lazy-loaded on first call (~500MB, cached in memory)
    │   Falls back to RRF order if unavailable
    │
    ▼
[6] Format & Return
    Citation: "{filename}, Section {chunk_index}"
    Quick Facts: "Quick Facts — {fact_key}"
    Filter by threshold (default 0.005)
```

**Fallback**: If all above fail → ILIKE keyword search on `document_chunks.content`.

---

## 7. Schema RAG — Smart Schema Selection

**Problem**: 43 CRM tables is too much context to dump into every LLM prompt.

**Solution** (`backend/app/rag/schema_rag.py`):

1. A `schema_descriptions` table stores one row per CRM table with a text description, column summary, relationships, common mistakes, and business synonyms.
2. Each row has an embedding (pre-computed, 768-dim).
3. At query time:
   - Embed the user's question
   - Vector search `schema_descriptions` → top 20 candidates
   - BGE rerank → top 10 (configurable via `top_k`)
   - Only those 10 table schemas go into the system prompt
4. **Result**: ~20× context reduction, better LLM focus, fewer hallucinated column names.

```sql
schema_descriptions:
  id, connector, table_name, table_description, columns_summary,
  relationships, common_mistakes, business_synonyms,
  embedding (vector(768))
```

---

## 8. 3-Tier Memory System

The agent learns from every interaction. Three tiers serve different purposes.

### 8.1 Tier 1 — Query Pattern Memory

**What it stores**: Every successful NL→SQL pair (with embedding of the question).

**Table**: `agent_memory`
```sql
id, question (text), sql_generated (text), success (bool),
response_type (text), embedding (vector(768)),
connector_id, tenant_id, created_at
```

**At retrieval time**: Embed current question → vector search → top 3 most similar past queries (similarity ≥ 0.85, scoped to tenant) → inject as dynamic few-shot examples.

**Storage guard** (BUG-D fix): Before storing a Q→SQL pair, validates:
- SQL contains `WHERE tenant_id = :tenant_id`
- All soft-delete filters present (`deleted_at IS NULL` on non-auth tables)
- Missing guards = refused storage

### 8.2 Tier 2 — Error Memory

**What it stores**: Every failed SQL execution (with error message and corrected SQL if available).

**Table**: `agent_error_log`
```sql
id, failed_sql (text), error_message (text), corrected_sql (text),
question (text), connector_id, created_at
```

**At retrieval time**: Fetch recent 5 errors → inject into system prompt as "DO NOT repeat these SQL patterns" with BAD SQL / Error / CORRECT SQL format.

**Effect**: The agent never repeats the same SQL mistake twice.

### 8.3 Tier 3 — Connector Knowledge

**What it stores**: Persistent learned facts about the CRM — enum values, column notes, join patterns, tips.

**Table**: `connector_knowledge`
```sql
id, connector_id, fact_type, fact_key, fact_value,
tenant_id, created_at
UNIQUE(tenant_id, connector_id, fact_type, fact_key)
```

**Fact types**:
- `enum_value` — e.g., "enquiry status values: 'lead_in', 'in_progress', 'quoted', 'converted', 'lost'"
- `column_name` — notes about specific columns
- `relationship` — table join patterns
- `tip` — query optimization tips

**At retrieval time**: All facts for this tenant+connector → formatted text block → injected into system prompt.

**Also searchable**: Keyword search on fact_key + fact_value, returned as search results competing with documents.

---

## 9. Personal Memory & Onboarding

### 9.1 Tables

**`user_memory`** (per tenant + user):
```sql
tenant_id, user_id, display_name, role,
preferred_format  -- 'auto' | 'chart' | 'table' | 'text'
topics_of_interest (text[]),
agent_name        -- default "DW AI"
proactive_preference  -- 'none' | 'morning' | 'evening' | 'alerts'
interaction_count, last_interaction_at,
onboarding_step (0–4), onboarding_complete (bool),
preferences (jsonb), created_at, updated_at
```

**`company_memory`** (per tenant):
```sql
tenant_id, company_name, industry, preferred_currency, created_at, updated_at
```

### 9.2 First-Time Onboarding (5-Step Flow)

New users are guided through onboarding before their first real query:

| Step | Question | Stores |
|------|----------|--------|
| 0 | "What should I call you?" | `display_name` |
| 1 | "What do you mainly track?" | `topics_of_interest` |
| 2 | "Charts, tables, or text?" | `preferred_format` |
| 3 | "What would you like to call me?" | `agent_name` |
| 4 | "Morning or evening updates?" | `proactive_preference` |

After step 4: `onboarding_complete = true`, onboarding skipped for all future queries.

### 9.3 Natural Language Preference Updates

No LLM needed — regex patterns detect preference changes directly:
- "call me Raj" → updates `display_name`
- "my name is Priya" → updates `display_name`
- "I prefer charts" → updates `preferred_format`

Returns confirmation immediately, bypasses the full agent loop.

---

## 10. SQL Tool & Query Execution

### 10.1 Tool: `dwerp_sql_query`

File: `backend/app/agent/tools.py`

The agent calls this tool to fetch CRM data. It generates a SQL SELECT and this tool executes it.

**4-Layer Security**:

```
SQL string from LLM
        │
[Layer 1] validate_sql()
        ├── Must start with SELECT or WITH
        ├── Block: INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, MERGE
        ├── Block: SQL comments (--, /*, */)
        ├── Must contain 'tenant_id' or '$'
        └── Max 1 statement (no semicolon chaining)
        │
[Layer 2] validate_sql_output()
        ├── Re-check for DML after semicolons
        └── Block CTEs that wrap DML (CTE injection defense)
        │
[Layer 3] tenant_id_var context variable
        ├── Set at request start from JWT
        ├── Injected as :tenant_id parameter
        └── Prevents cross-tenant queries
        │
[Layer 4] mask_pii()
        └── Masks: salary, ssn, aadhaar, pan_number, bank_account, etc.
```

**Execution flow**:
```python
# asyncpg pool from connection pool
rows = await execute_query(sql, params={"tenant_id": tenant_id})

# Returns JSON:
{
  "rows": [...],
  "columns": ["col1", "col2"],
  "row_count": N,
  "confidence": "ai_generated",
  "source": "llm"
}
```

**Error handling**:
- First failure → return error to agent, agent retries with corrected SQL
- Second failure → "I had trouble finding that data. Try rephrasing."
- Both errors stored in Tier 2 error memory
- Raw SQL is NEVER shown to the user

### 10.2 Critical SQL Rules (Non-Negotiable)

Every generated SQL must have:

```sql
WHERE tenant_id = :tenant_id          -- data isolation, always
AND deleted_at IS NULL                 -- soft-delete filter (all tables except auth_user)
AND is_active = true                   -- for auth_user (no deleted_at column)
AND status != 'superseded'            -- for quotations (exclude old revisions)
```

**GenericForeignKey joins** (Django-style, V2):
```sql
-- To join common_follow_ups to enquiries:
JOIN django_content_type ct ON ct.id = cfu.content_type_id
WHERE ct.app_label = 'enquiry_app' AND ct.model = 'enquirymodel'
  AND cfu.object_id = enquiry_id
  AND cfu.is_deleted = false

-- To join common_follow_ups to quotations:
WHERE ct.app_label = 'quotation_app' AND ct.model = 'quotationmodel'
```

---

## 11. Chart & Visualization Detection

File: `backend/app/agent/graph.py` → `_detect_all_charts()`

### 11.1 Column Classification

| Column type | Detection keywords |
|-------------|-------------------|
| Category | status, name, organization, type, stage |
| Numeric | count, value, revenue, total, amount |
| Time | week, month, date, day, quarter, created_month |

### 11.2 Chart Selection Rules

| Data shape | Chart type |
|-----------|-----------|
| Time + numeric | Line chart |
| Category + numeric | Bar chart |
| Category + count, ≤8 categories | Pie/donut |
| Single row | KPI cards (no chart) |
| >60% zero values | Skip chart |

Up to 3 charts per response. Never duplicate the same dimension (e.g., two bar charts of status counts).

### 11.3 Frontend Chart Config Format

```json
{
  "type": "bar",
  "config": {
    "xKey": "status",
    "yKey": "count",
    "title": "Count by Status"
  },
  "data": [
    {"status": "in_progress", "count": 12},
    {"status": "ready", "count": 18}
  ]
}
```

---

## 12. API Endpoints & Streaming

### 12.1 Main Chat Endpoint

**POST `/api/chat`** — Server-Sent Events (SSE)

**Request body**:
```json
{
  "message": "Show me this month's pipeline by status",
  "conversation_id": "uuid (optional)",
  "connector": "dwerp",
  "mode": "all",          // "all" | "crm" | "knowledge"
  "persona": "smart",     // "professional" | "smart" | "casual"
  "thread_id": "uuid (optional)"
}
```

**SSE stream format**:
```
event: message
data: {"type": "status", "content": "Analyzing your question...", "query_id": "uuid"}

event: message
data: {"type": "text", "content": "You have 42 quotations...", "model_used": "...", "query_id": "uuid"}

event: message
data: {"type": "kpi", "data": {"items": [{"label": "Total", "value": 42}]}, "query_id": "uuid"}

event: message
data: {"type": "chart", "data": {"type": "bar", "config": {...}, "data": [...]}, "query_id": "uuid"}

event: message
data: {"type": "table", "data": {"columns": [...], "rows": [...]}, "query_id": "uuid"}

event: done
data: {}
```

**StreamChunk `type` values**:
- `text` — plain English answer
- `kpi` — metric cards (label + value)
- `chart` — bar/line/pie chart with data
- `table` — multi-row tabular data
- `action` — proposed write action (future, needs confirmation)
- `status` — progress update ("Analyzing...", "Running query...")
- `error` — error message

### 12.2 All Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| POST | `/api/auth/login` | Get JWT token |
| POST | `/api/chat` | Main chat (SSE stream) |
| GET | `/api/chat_history` | Fetch past conversation messages |
| GET | `/api/connectors` | List available connectors + schemas |
| POST | `/api/upload` | Upload document (PDF/DOCX/XLSX) |
| POST | `/api/voice` | Voice input → transcription |
| GET | `/api/admin/memory/stats` | Tier 1 memory stats |
| GET | `/api/admin/error/stats` | Tier 2 error memory stats |
| GET | `/api/admin/knowledge/stats` | Tier 3 knowledge facts stats |
| GET | `/api/admin/audit/logs` | Audit log (tokens, latency, model, user) |

---

## 13. Authentication & Multi-Tenancy

### 13.1 JWT Authentication

**Login endpoint**: `POST /api/auth/login` → `{access_token, user}`

**Token validation flow** (`backend/app/core/security.py`):
1. Try MSBC Agent's own JWT (signed with `JWT_SECRET`)
2. Try DWERP Supabase JWT (signed with `DWERP_JWT_SECRET`)
3. Dev fallback: Decode without signature verification

**Tenant resolution**:
- From JWT claim `tenant_id` (MSBC Agent tokens)
- OR from DB lookup by user_id / email (Supabase tokens)
- `X-Tenant-ID` header override supported (with access validation before applying)

### 13.2 Multi-Tenant Data Isolation (5 Layers)

| Layer | Mechanism |
|-------|----------|
| JWT | `tenant_id` encoded in token |
| Context var | `tenant_id_var` set at request start, read by tools |
| SQL param | `:tenant_id` in all queries, never injected as string |
| Memory scoping | All memory tables filtered by `tenant_id` |
| Override validation | Header override only allowed if user belongs to requested tenant |

### 13.3 Prompt Injection Defense

File: `backend/app/core/security.py` → `check_injection()`

Blocked patterns:
- "ignore previous instructions"
- "you are now a..."
- "disregard all rules"
- "cross-tenant", "other company data"
- "bypass security", "admin mode", "debug mode"

Also strips HTML tags and embedded markdown system prompts before processing.

---

## 14. Caching Strategy

### 14.1 Response Cache (2 Tiers)

**Tier 1 — Exact Cache**:
- Key: `(question, tenant_id, mode, persona)`
- Value: Full list of SSE chunks (text + charts + KPIs)
- TTL: 300 seconds (configurable via `CACHE_TTL_SECONDS`)
- Backend: Redis

**Tier 2 — Semantic Cache**:
- Key: `(question_embedding, tenant_id, mode, persona)`
- Threshold: ~0.95 cosine similarity
- Catches rephrased versions of the same question

On cache hit: All chunks returned with `cached=true`, full agent skipped, audit log entry created immediately.

### 14.2 Per-Query RAG Cache (W7f Fix)

**Problem**: LangGraph ReAct loop runs 4–6 iterations per query. Each iteration was re-running Schema RAG (~3 seconds).

**Solution**: Cache RAG results in agent state keyed by `f"{query_id}|{hash(user_msg)}|{tenant_id}|{connector}"`.

**What's cached**:
- `schema_desc` — selected table schemas
- `examples_cache` — few-shot examples
- `memory_examples` — Tier 1 past queries
- `error_examples` — Tier 2 error patterns
- `knowledge_text` — Tier 3 facts

First iteration runs full RAG; all subsequent iterations reuse the cache. ~20–30% latency reduction on complex queries.

### 14.3 Performance Configuration

```bash
MSBC_AGENT_MAX_ITERS=5        # Max ReAct loop iterations
CACHE_TTL_SECONDS=300          # Response cache TTL
DATABASE_POOL_SIZE=10          # asyncpg min connections
DATABASE_POOL_MAX=20           # asyncpg max connections
```

Step timeout: 120 seconds per graph step (covers reranker cold-start).

---

## 15. DWERP CRM Database Schema

### 15.1 Critical Column Names (Common Mistakes)

| What you want | CORRECT column | WRONG (will error) |
|--------------|---------------|-------------------|
| Quotation value | `grand_total` | `total_value` |
| Enquiry title | `project_name` | `title` |
| User's name | `full_name` | `name` |
| Active users filter | `is_active = true` | `deleted_at IS NULL` |
| Activity timestamp | `changed_at` | `created_at` |
| Activity actor | `changed_by` | `created_by` |
| Survey link | `quotation_id` | `enquiry_id` |
| Organization city | `primary_addr_city` | `city` |

### 15.2 Key Tables

**`organizations`** (83 rows)
- `id, tenant_id, name, org_type, branch`
- `org_type`: Customer, Builder/Developer, Architect/Designer, Channel Partner, Vendor/Supplier, Retail/End Customer
- `categories` (array): Residential, Commercial, Industrial, Hospitality, Facade Projects
- `lead_score (int), lead_temperature`: hot / warm / cold
- `primary_addr_city` — city field (NOT `city`)
- `primary_sales_person → auth_user`

**`enquiries`** (94 rows)
- `id, tenant_id, enquiry_number (text), enquiry_date (date)`
- `organization_id → organizations`
- `status`: lead_in, in_progress, quoted, converted, lost
- `enquiry_type`: New Construction, Residential, Commercial, Industrial, Maintenance, Repeat Order
- `enquiry_source`: Direct, Referral, Website, Walk-in, Architect, Builder, Channel Partner, Tender
- `project_name` — the enquiry title (NOT `title`)
- `sales_representative, estimator_id → auth_user`
- Soft-delete: `deleted_at IS NULL`

**`quotations`** (71 rows)
- `id, tenant_id, quotation_number, quotation_date, validity_date`
- `enquiry_id → enquiries, organization_id → organizations`
- `status`: in_progress, ready, waiting_for_verification, verified, waiting_for_approval, authorized, sent, customer_negotiation, customer_confirmed, revision_required, lost, rejected, expired, superseded
- **`grand_total (numeric)`** — THIS is the quotation value
- Always filter: `AND status != 'superseded'` (excludes old revisions)
- Soft-delete: `deleted_at IS NULL`

**`quotation_positions`** — line items
- `id, quotation_id, tenant_id`
- `product_type`: Window, Door, Sliding Door, Casement Window, Fixed Window, Curtain Wall, Skylight
- `unit_price, total_price (numeric)`
- `quantity, width, height, color (text)`

**`common_follow_ups`** (Django GenericForeignKey V2)
- `content_type_id (int) + object_id (uuid)` — links to any model
- `followup_type`: call, email, visit, whatsapp, meeting
- `status`: pending, completed, rescheduled, missed
- `assigned_to → auth_user`
- Soft-delete: `is_deleted = false` (NOT `deleted_at IS NULL`)

**`site_surveys`** (17 rows)
- **Links via `quotation_id → quotations`** (NOT enquiry_id)
- `status`: scheduled, in_progress, completed, design_update_required, closed
- `engineer_id → auth_user`

**`jobs`** (0 rows — table exists, no data yet)
- `id, job_number, job_date`
- `quotation_id, enquiry_id, organization_id`
- `status` default: `pending_payment`

**`common_status_history`** (Django GenericForeignKey V2, replaces `activities`)
- `content_type_id + object_id` — GFK pair
- `change_type`: created, updated, status_changed
- `previous_value, new_value (text)` — before/after values
- `changed_by → auth_user` (V1 was `performed_by`)
- `changed_at (timestamptz)` (V1 was `performed_at`)

**`auth_user`** (renamed from `users` in W6 2026-05-13)
- `id, tenant_id, email (varchar), full_name (varchar)`
- `is_active (bool)` — filter with this, NOT `deleted_at IS NULL`
- No `role` column — roles are in `msbc_rbac.accounts.UserRole`

**`organization_contacts`** (48 rows)
- `contact_name, designation, department`
- `mobile, alternate_mobile, email, whatsapp_enabled`
- `is_primary, is_active`

### 15.3 Mandatory SQL Filters

```sql
-- Always (data isolation)
WHERE tenant_id = :tenant_id

-- Always on tables with soft-delete (everything except auth_user)
AND deleted_at IS NULL

-- For auth_user specifically (no deleted_at)
AND is_active = true

-- For quotations always (exclude old revisions)
AND status != 'superseded'

-- For common_follow_ups (different soft-delete pattern)
AND is_deleted = false
```

---

## 16. Deployment (Docker + Jenkins)

### 16.1 Docker Setup

**Dockerfile** (in `backend/`):
```dockerfile
FROM python:3.12-slim
# Installs system deps (curl), Python requirements
# Exposes port 8001
# Health check: curl http://localhost:8001/health
# CMD: uvicorn app.main:app --host 0.0.0.0 --port 8001
```

**docker-compose.yml** (in `docker/`):
- `msbc-agent` — backend (port 8001, `--restart unless-stopped`)
- `redis` — `redis:7-alpine` (health-checked before backend starts)
- Both on `dwerp-network` bridge

### 16.2 Jenkins Pipeline

The `Jenkinsfile` runs these stages:
1. **Build**: `docker build -f backend/Dockerfile backend`
2. **Push**: `docker push nexus.msbcgroup.com/dwerp-agent:latest`
3. **Migrate**: Run all `/backend/migrations/*.sql` files sequentially against DB
4. **Deploy**: `docker run --restart unless-stopped -p 8001:8001 --env-file agent.env <image>`

Migration command:
```bash
for m in /migrations/*.sql; do
  psql -h 192.168.71.90 -U dwerp_new -d dwerp_new -v ON_ERROR_STOP=1 -f $m
done
```

### 16.3 Environment Variables Summary

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/dwerp_new
DATABASE_POOL_SIZE=10
DATABASE_POOL_MAX=20

# Redis
REDIS_URL=redis://localhost:6379/1
CACHE_TTL_SECONDS=300

# LLMs
OPENROUTER_API_KEY=sk-or-v1-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
TIER1_MODEL=anthropic/claude-haiku-4-5
TIER2_MODEL=google/gemini-3.1-flash-lite-preview
TIER3_MODEL=x-ai/grok-4.1-fast
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=deepseek-r1:32b

# Auth
JWT_SECRET=...              # min 32 chars
JWT_ALGORITHM=HS256
JWT_EXPIRY_HOURS=24
DWERP_JWT_SECRET=...        # Supabase JWT secret

# CORS
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# Tuning
MSBC_AGENT_MULTI_TIER=false    # true to re-enable 3-tier routing
MSBC_AGENT_MAX_ITERS=5         # ReAct recursion limit
MAX_UPLOAD_SIZE_MB=50
RATE_LIMIT_PER_MINUTE=60
DEBUG=true
ENVIRONMENT=development
```

---

## 17. Database Migrations

All migration files in `backend/migrations/`. Run in order.

| File | Creates |
|------|---------|
| `003_memory_and_audit.sql` | `agent_memory`, `agent_error_log`, `connector_knowledge`, `audit_logs_persistent` + pgvector extension |
| `005_documents.sql` | `uploaded_documents`, `document_chunks` |
| `006_hybrid_search.sql` | Full-text search index (`content_tsv`) |
| `007_chunk_metadata.sql` | `page_number`, `word_count`, `language` columns on `document_chunks` |
| `008_dedup.sql` | Deduplication MD5 hash column |
| `009_user_memory.sql` | `user_memory`, `company_memory` |
| `010_schema_rag.sql` | `schema_descriptions` table + HNSW vector index |
| `011_conversations.sql` | `conversations` table for LangGraph thread history |
| `012_website_connector.sql` | `website_pages`, `website_chunks` (web crawling support) |
| `013_connector_knowledge_tenant_id.sql` | Adds `tenant_id` to `connector_knowledge` |

---

## 18. End-to-End Query Walkthroughs

### 18.1 Simple: "How many enquiries do we have?"

1. JWT validated → tenant_id extracted
2. No injection detected
3. Not conversational
4. Cache miss (first ask)
5. Personal memory loaded (display_name, preferred_format)
6. System prompt assembled: smart persona, Schema RAG returns `enquiries` + `organizations` schemas, 3 similar past queries from Tier 1 memory
7. LangGraph starts ReAct loop → LLM on iteration 1 decides to call `dwerp_sql_query`
8. SQL generated: `SELECT count(*) as total FROM enquiries WHERE tenant_id = :tenant_id AND deleted_at IS NULL`
9. Tool validates SQL → executes → returns `[{"total": 94}]`
10. LLM iteration 2: has the answer, emits final response (no more tools)
11. Response chunks streamed: `status` → `text` ("You have 94 enquiries") → `kpi` (Total = 94) → `done`
12. Q→SQL stored in Tier 1 memory
13. Response cached (exact + semantic)
14. Audit log entry written

### 18.2 Complex: "Analyze pipeline health"

1. Auth + prompt injection check
2. Not conversational, cache miss
3. Complexity classified as "complex" (keyword "analyze")
4. Full system prompt assembled with all 43 tables, error patterns, few-shot
5. ReAct loop, iteration 1: LLM calls `dwerp_sql_query` for quotations by status + value
6. Result: 5 rows `{status, count, grand_total}`
7. Iteration 2: LLM calls `dwerp_sql_query` for monthly enquiry trend
8. Result: 3 rows `{month, count}`
9. Iteration 3: LLM has enough data → writes multi-paragraph analysis, no more tool calls
10. Chart detection: row 1 results → bar chart (status vs count); row 2 results → line chart (trend)
11. Streamed chunks: `text` + `chart` (bar) + `chart` (line) + `kpi` (pipeline value)
12. Both SQL queries stored in Tier 1 memory; full response cached

### 18.3 Conversational: "Hi there!"

1. Fast-path detection triggers
2. Grok 4.1 Fast called with minimal system prompt (user name + agent name only)
3. No tools, no RAG, no memory retrieval
4. Response: "Hey! Doing great. What can I help with today?"
5. Total time: <100ms

### 18.4 Document search: "What is the minimum glass thickness per our specs?"

1. Agent determines this is a knowledge question, not a CRM data question
2. LLM calls `search_documents("minimum glass thickness")`
3. Search pipeline runs (embed → dense → sparse → RRF → BGE rerank)
4. Top 5 chunks returned from `Glass Specifications.pdf`
5. LLM synthesizes answer with citation: "Glass Specifications.pdf, Section 2"
6. Streamed as `text` chunk with citation

---

## 19. Monitoring & Debugging

### 19.1 Log Patterns

```
INFO  | msbc.agent.graph   | Routing to anthropic/claude-haiku-4-5 (complexity: medium)
INFO  | msbc.rag.schema    | Schema RAG returned 8 tables for: Show pipeline
INFO  | msbc.agent.tools   | dwerp_sql_query tenant=xxx rows=5 latency=230ms
INFO  | msbc.memory.query  | Memory recall: 3 similar (top: 0.92) for: Show pipeline
INFO  | msbc.chat          | Completed: 2500ms | model=claude-haiku-4-5 | user=dhruv@...
INFO  | msbc.chat          | Completed: 123ms | model=cache | user=demo@...
```

### 19.2 Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| 401 "User not found" | JWT expired or OpenRouter key expired | Regenerate `OPENROUTER_API_KEY` in `agent.env` |
| "No tenant context" error | JWT missing `tenant_id` claim | Check `security.py` `_lookup_tenant()`, verify `auth_user` table has row |
| Charts not rendering | Chart detection failed to find numeric columns | Check `_detect_all_charts()` in `graph.py` |
| Same SQL error keeps recurring | Error not stored in Tier 2 | Check `agent_error_log` table, may be disk full |
| First query very slow (~10s) | BGE reranker not preloaded | Check `app.main` lifespan runs `preload_model()` at startup |
| Document search returns nothing | pgvector index missing | Run migration `005_documents.sql`, check IVFFlat indexes exist |
| Agent counts include deleted records | Missing soft-delete filter | Check SQL in logs, ensure `validate_sql()` is enforcing this |
| Cross-tenant data appearing | `tenant_id` filter missing | Audit SQL logs immediately, check `tenant_id_var` context variable is set |

### 19.3 Admin Stats Endpoints

```
GET /api/admin/memory/stats
  → {total_memories, with_embedding, recent_learnings}

GET /api/admin/error/stats
  → {total_errors, corrected, recent_errors}

GET /api/admin/knowledge/stats
  → {total_facts, by_type: {enum_value: N, column_name: N, ...}}

GET /api/admin/audit/logs?limit=100
  → [{user_id, question, model_used, latency_ms, tokens, cost, created_at}]
```

---

## 20. Known Limitations & Future Work

### 20.1 Not Yet Implemented

| Feature | Status | Notes |
|---------|--------|-------|
| Write actions | Schema exists, not wired | `propose_action` tool returns proposal but frontend confirmation + execution is not built |
| RBAC | Schema exists, not wired | All users get same role ("authenticated") from JWT; `msbc_rbac.accounts.UserRole` not queried |
| Proactive updates | Preference stored, not sent | No background job; would require Celery/RQ + cron |
| User feedback loop | Not built | No "this answer was wrong" → store corrected SQL flow |

### 20.2 Optimization Opportunities

| Area | Idea |
|------|------|
| Tokens | Use Claude's prompt caching for static schema context (large, rarely changes) |
| Latency | Parallelize memory retrieval (Tier 1 + Tier 2 + Tier 3 simultaneously) |
| Cost | Route simple queries to local Ollama (zero OpenRouter cost) |
| Accuracy | Fine-tune on DWERP-specific SQL patterns (LoRA) |
| Reliability | Add automated SQL correctness tests against test data |

---

## 21. Key Files Quick Reference

| Task | File | Key Function/Constant |
|------|------|-----------------------|
| Add a new CRM table to schema | `backend/app/connectors/dwerp.py` | `SCHEMA_CONTEXT`, `HIGH_VALUE_TABLES` |
| Fix or extend SQL validation | `backend/app/agent/tools.py` | `validate_sql()`, `validate_sql_output()` |
| Modify system prompt | `backend/app/agent/prompts.py` | `build_system_prompt()`, `PERSONA_PROMPTS` |
| Change LLM routing | `backend/app/agent/router.py` | `classify_complexity()`, `FORCE_TIER1` |
| Tune chart detection | `backend/app/agent/graph.py` | `_detect_all_charts()` |
| Modify RAG search | `backend/app/rag/search.py` | `hybrid_search()` |
| Change chunking strategy | `backend/app/rag/chunker.py` | `chunk_text()` |
| Modify embedding | `backend/app/rag/embedder.py` | `embed_texts()` |
| Tune Schema RAG | `backend/app/rag/schema_rag.py` | `get_relevant_schemas()` |
| Modify Tier 1 memory | `backend/app/memory/query_memory.py` | `store_successful_query()`, `recall_similar_queries()` |
| Modify personal memory | `backend/app/memory/user_memory.py` | `get_user_memory()`, onboarding logic |
| Change auth/tenant logic | `backend/app/core/security.py` | `get_current_user()`, `_lookup_tenant()` |
| Add new API endpoint | `backend/app/api/` | FastAPI routers |
| Run DB migrations | `backend/migrations/*.sql` | Execute in numbered order via psql |
| Update Docker config | `docker/docker-compose.yml` | Service definitions |
| Update CI/CD | `Jenkinsfile` | Build/push/migrate/deploy stages |









# DW AI in DWERP - End-to-End ERP Use Case Report

Date: 2026-05-18
Author: Codex / Nova QA review lane
Scope: Product and business-use-case diagnosis. This report intentionally avoids deep technical design and focuses on what DW AI should let users do inside the ERP.

## Reader Decision Snapshot

If Manu, Mehul, or the product team only reads one page, the decision should be:

1. Approve DW AI as an ERP operating assistant, not a chatbot.
2. Start with the "DW AI Work Update" product slice.
3. Build typed input first, voice second, using the same action/confirmation flow.
4. Allow AI to read, explain, summarize, draft, and prepare updates.
5. Require confirmation for any update that touches stock, money, customer communication, production completion, delivery proof, sign-off, or approvals.
6. Launch the first demo around one real job: enquiry -> quote -> job blocker -> stock issue -> production update -> payment reminder.
7. Treat this as a premium differentiator for manufacturing/fenestration ERP.

The first measurable promise should be:

> "DWERP users can update work, capture notes, and understand blockers in seconds without opening every module screen."

## 1. Executive Summary

DWERP already has the right foundation to become an AI-native ERP for fenestration and manufacturing. The current docs show voice input, DW AI chat, AI search, AI suggestions, lead scoring, WhatsApp, audit trails, status histories, approval workflows, site photos, GPS, barcode tracking, payment milestones, and production progress. The missing layer is a clear business action model: what an end user can ask DW AI to do, what DW AI can safely update, and where the ERP must still ask for confirmation.

The product idea is strong:

> If a user has permission, they should be able to update the ERP by typing, speaking, or selecting from AI-suggested actions.

This should not be positioned as "chatbot inside ERP." It should be positioned as:

> DW AI is the operating assistant for fenestration work. It helps users capture updates, move records forward, detect blockers, prepare documents, follow up with customers, and keep managers informed without forcing everyone to open 10 screens.

The strongest initial focus should be:

1. Task/progress updates.
2. Follow-up creation and completion.
3. Status change assistance.
4. Job blocker explanation.
5. Stock and production exception reporting.
6. Sales and finance reminders.
7. Daily owner/manager briefing.
8. Voice/mobile updates for field and factory users.

The safest product principle:

> DW AI can draft and recommend many actions, but any action that changes money, stock, legal status, customer communication, delivery proof, or production completion must show a preview and require confirmation.

## 2. Evidence From Current DWERP Docs

The current product documentation already supports this direction.

- DWERP module map is end-to-end: Organization -> Enquiry -> Estimator -> Quotation -> Site Survey -> Job -> PO -> Warehouse/Stock -> Production -> Delivery -> Finance -> Reporting (`specs/00-INDEX.md:9-24`).
- v2.0 already names AI, audit trail, WhatsApp, geo-tagging, authorization workflows, lead scoring, duplicate detection, anomaly detection, product configurator, e-Quote, and natural-language search as system features (`specs/00-INDEX.md:35-39`).
- Platform feature list says voice input, AI suggestions, lead scoring, smart search, weekly report generation, WhatsApp Business API, PDF generation, and calendar integration already exist or are planned (`FEATURES.md:19-50`).
- Voice navigation was already proposed as a differentiator, with a clear principle: "If you can talk, you can use DWERP" (`IMPROVEMENTS.md:88-115`).
- The frontend already routes unmatched voice/search text into DW AI chat rather than treating voice only as navigation (`dw-erp-frontend/src/components/ui/VoiceCommandPalette.tsx:300-362`).
- DW AI chat already has a confirmation-card pattern for actions before execution (`dw-erp-frontend/src/components/common/ChatSidebar.tsx:682-703`).
- Backend AI already has AI Suggestions, Lead Scoring, Smart Search, Quotation AI Summary, and Weekly Report endpoints (`backend/app/api/v1/ai/routes.py:40-110`).
- Current smart search deliberately avoids raw SQL generation and extracts structured intent instead (`backend/app/api/v1/ai/search.py:22-49`). This is exactly the right mindset for AI actions too.
- Current suggestions are deterministic and SQL-driven for reliability: follow-up reminders, price anomaly, pipeline alert, payment approaching, cold lead (`backend/app/api/v1/ai/suggestions.py:20-31`).

Conclusion: DW AI should evolve from "search/help/summary" into "guided action assistant", but it should keep the structured, permission-aware, confirmation-first pattern already visible in the codebase.

## 3. Product Thesis

Manufacturing ERP fails when the real worker does not update the system in time.

In fenestration, the user base is not only office staff. It includes:

- Salespeople.
- Estimators.
- Survey engineers.
- Storekeepers.
- Purchase team.
- Production supervisors.
- Factory workers.
- Dispatch team.
- Installation teams.
- Accountants.
- Owners and managers.

Many of them are not sitting at a desktop all day. Some are on factory floors, in warehouses, on roads, or at sites. For them, the fastest ERP interface is not a form. It is:

- Speak.
- Select.
- Confirm.
- Done.

DW AI should make ERP updates feel like WhatsApp-simple, but with enterprise controls.

## 4. DW AI Capability Levels

Use five levels. This keeps scope controlled and prevents overpromising.

### Level 1 - Ask

User asks questions. DW AI reads ERP data and answers.

Examples:

- "Which jobs are blocked today?"
- "Show enquiries with no follow-up this week."
- "What stock is below reorder level?"
- "Which dispatches are delayed?"
- "Which invoices are overdue?"

Risk: low.

Confirmation needed: no.

### Level 2 - Explain

DW AI explains why something is blocked or delayed.

Examples:

- "Why can't Job JB-1042 start production?"
- "Why is this PO not ready for GRN?"
- "Why is this quotation still waiting for approval?"
- "Why is usable stock less than live stock?"

Risk: low.

Confirmation needed: no.

### Level 3 - Draft

DW AI prepares an action but does not apply it.

Examples:

- Draft a follow-up note.
- Draft WhatsApp message.
- Draft PO line items from job BOM.
- Draft stock adjustment reason.
- Draft site survey summary.
- Draft invoice reminder.

Risk: medium.

Confirmation needed: yes before send/save when the content affects customer, money, or inventory.

### Level 4 - Update

DW AI updates the ERP after preview and confirmation.

Examples:

- Mark task progress to 60%.
- Complete follow-up.
- Reschedule survey.
- Update delivery status.
- Add dependency between tasks.
- Record production stage completion.
- Add site note/photo description.

Risk: medium to high.

Confirmation needed: yes for most updates.

### Level 5 - Recommend And Automate

DW AI recommends or triggers workflows based on rules.

Examples:

- Recommend PO because stock is below reorder point.
- Recommend rescheduling installation due to weather.
- Recommend escalation for overdue payment.
- Recommend manager review for large stock adjustment.
- Recommend supplier quality alert.

Risk: high.

Confirmation needed: yes. Some alerts can be automatic; business changes should not.

## 5. Primary Interface Model

DW AI should appear in four places, not one.

### 5.1 Global DW AI Command Bar

Always available at the top or through keyboard shortcut.

Use cases:

- Search.
- Ask.
- Navigate.
- Create small updates.
- Start module actions.

Example commands:

- "Open Marina Bay quotation."
- "Create follow-up for Gulf Glass tomorrow."
- "Show jobs blocked by payment."
- "What needs approval today?"
- "Set fabrication task to 60 percent."

### 5.2 Module-Specific AI Panel

Each major page should have a right-side DW AI panel that understands the current record.

On a Job page, it knows the job, materials, production, delivery, invoices.

On a Stock page, it knows material, warehouse, live stock, usable stock, allocation, movements.

On a Quotation page, it knows positions, glass, profiles, hardware, taxes, status, revisions.

This is where DW AI becomes truly useful because the user does not need to explain context.

### 5.3 Voice / Mobile Quick Update

This is for field and factory users.

Examples:

- Survey engineer: "Bedroom window measured. Width 1210, height 1490. Add note: sill not level."
- Warehouse user: "Issue five lengths of PRF-101 to job 1042."
- Production worker: "Mark cutting complete for barcode 88421."
- Installation lead: "Weather hold. High wind. Resume tomorrow 9 AM."

### 5.4 Manager Morning Brief

Owner/manager opens dashboard and DW AI gives a concise operational readout:

- What is blocked.
- What is overdue.
- What changed yesterday.
- What needs approval.
- What is at risk today.
- Which customer needs attention.
- Which stock item may stop production.

This is likely the highest-value executive use case.

## 6. Access-Aware Action Rules

DW AI must respect the same permissions as the ERP.

Suggested action classes:

| Action class | Example | Who can confirm |
|---|---|---|
| Personal update | Complete my follow-up | Assigned user |
| Record note | Add internal note | User with module edit access |
| Status move | Move enquiry to quoted | Module owner / allowed role |
| Dependency update | Make installation depend on dispatch | Project/job owner or manager |
| Stock movement | Issue/return/adjust stock | Warehouse authorized role |
| Money action | Finalize invoice, credit note, PO approval | Finance/manager approver |
| Customer communication | Send WhatsApp/email | Owner of record or approved role |
| Legal/customer sign-off | e-signature, POD, completion | Manual/customer confirmation only |

Rule:

> If the user cannot do it by hand, DW AI cannot do it for them.

## 7. Minimum Smart AI V1

The first version should not try to automate everything. It should focus on common low-risk actions that save time immediately.

### V1.1 - Universal Read And Explain

DW AI answers:

- "What is pending for me?"
- "What changed today?"
- "Which jobs are blocked?"
- "Which customers need follow-up?"
- "Which POs are overdue?"
- "Which stock items are critical?"

Value: managers and operators get immediate clarity without opening reports.

### V1.2 - Follow-Up Assistant

DW AI can create, complete, reschedule, and summarize follow-ups.

Examples:

- "Create follow-up for Ali tomorrow at 11 about quote approval."
- "Mark this follow-up done. Customer asked for revision."
- "Reschedule all my missed follow-ups to tomorrow morning."

Why first: follow-ups are frequent, low risk, and already exist across CRM, quotation, jobs, finance, and after-sales.

### V1.3 - Task And Progress Assistant

DW AI can update task progress and dependencies after preview.

Examples:

- "Set cutting task to 80 percent."
- "Mark site readiness checklist complete except scaffolding."
- "Make installation depend on delivery challan completion."
- "Block production until advance payment clears."

Why first: this becomes the operating layer for every module.

### V1.4 - Notes And Summary Assistant

DW AI captures messy user language into clean ERP notes.

Examples:

- "Add note: customer wants black hardware and wants quote revised by Friday."
- "Summarize this site survey for the estimator."
- "Summarize today's production blockers."

Why first: users hate typing notes, but notes are the source of truth.

### V1.5 - Approval Assistant

DW AI shows what needs approval and prepares decision context.

Examples:

- "What should I approve today?"
- "Why is this PO high risk?"
- "Approve PO-1021 because rate is within last purchase range."
- "Reject with note: price exceeds approved vendor rate."

Confirmation: always required.

## 7A. First 10 Buildable AI Actions

These are the first ten actions to build because they are useful, common, demo-friendly, and do not require the most dangerous automation.

| Priority | Action | Example user command | Confirmation | Why it should be early |
|---|---|---|---|---|
| 1 | Add internal note | "Add note: customer wants black hardware." | Apply preview | Very common, low risk, saves typing |
| 2 | Create follow-up | "Remind me to call Ali tomorrow at 11." | Apply preview | CRM value immediately visible |
| 3 | Complete follow-up | "Mark this follow-up done; customer asked for revision." | Apply preview | Keeps pipeline clean |
| 4 | Explain blocker | "Why is this job blocked?" | None | High manager value, no write risk |
| 5 | Update task progress | "Set cutting to 60 percent." | Apply preview | Core operating-layer concept |
| 6 | Add dependency/blocker | "Installation depends on delivery." | Apply preview | Proves AI can manage work sequencing |
| 7 | Draft WhatsApp | "Draft quote follow-up message." | Edit/send confirmation | Saves sales time; no auto-send |
| 8 | Draft stock issue | "Issue 5 PRF-101 to job 1042." | Strong confirmation | High value, but must protect stock |
| 9 | Draft payment reminder | "Draft reminder for overdue invoice 1021." | Edit/send confirmation | Finance value without bank/accounting risk |
| 10 | Daily manager brief | "What needs attention today?" | None | Best owner/manager demo moment |

Do not start with auto-approval, auto-invoice finalization, auto-stock adjustment, or autonomous customer messaging. Those can come later after trust is established.

## 8. Module-by-Module Use Cases

## 8.1 CRM / Organization / Enquiry

Current docs show Organization, Enquiry, Estimator, Quotation, and Site Survey are the live CRM chain, with follow-ups, status history, communication notes, lead score, duplicate detection, WhatsApp, and audit logs.

High-value DW AI use cases:

1. Capture enquiry from natural language.
   - "Create enquiry for Skyline Developers. Curtain wall, Dubai, estimated 2 crore, source WhatsApp."
   - DW AI fills the form, detects missing fields, and asks only for gaps.

2. Detect duplicate customer.
   - "Add ABC Glass as customer."
   - DW AI warns: "Possible duplicate: ABC Glass LLC, same phone last 8 digits."

3. Lead qualification.
   - "Is this lead worth urgent follow-up?"
   - DW AI explains score: value, source, recency, customer type, activity.

4. Follow-up automation.
   - "Remind me to call this lead on Friday."
   - "Mark follow-up done; customer asked for revised glass spec."

5. Status movement with reason.
   - "Move this enquiry to lost. Reason: competitor won on price."
   - DW AI previews lost reason, competitor, note, status update.

6. Sales manager view.
   - "Which sales rep has the weakest follow-up discipline?"
   - "Which enquiries are rotting?"
   - "Which lead source is converting best?"

7. Communication drafting.
   - "Draft WhatsApp to ask for missing drawings."
   - "Draft polite follow-up for quotation approval."

Initial action scope:

- Create follow-up.
- Complete follow-up.
- Add note.
- Draft WhatsApp.
- Recommend status change.
- Explain lead score.

Do not auto-send messages or mark lost without confirmation.

## 8.2 Estimator / Assignment

High-value use cases:

1. Workload balancing.
   - "Who should estimate this enquiry?"
   - DW AI checks current workload, branch, product type, geography.

2. Assignment explanation.
   - "Why was Ravi assigned?"
   - DW AI explains workload, skill, and location.

3. Reassignment.
   - "Move this estimate to Priya because Ravi is overloaded."
   - Preview required.

4. Queue risk.
   - "Which estimates are overdue?"
   - "Which estimator is becoming a bottleneck?"

Initial action scope:

- Suggest estimator.
- Create assignment note.
- Reassign with confirmation.
- Notify estimator.

## 8.3 Quotation

Quotation is one of the strongest DW AI opportunities because the module has complex positions, glazing, profiles, hardware, approval, revision, expiry, follow-up, and document rules.

High-value use cases:

1. Quote summary.
   - "Summarize this quotation for customer call."
   - "What changed from revision 2 to revision 3?"

2. Missing data check.
   - "Is this quotation ready to send?"
   - DW AI checks positions, profiles, glass, hardware, tax, T&C, approval status, documents.

3. Approval reasoning.
   - "Why does this quote need approval?"
   - "Show margin risk and discount reason."

4. Revision drafting.
   - "Create revision because customer changed glass to double glazing."
   - DW AI drafts revision reason and affected lines.

5. Follow-up.
   - "Schedule follow-up 3 days after sending."
   - "Draft WhatsApp with e-quote link."

6. Customer response handling.
   - "Customer confirmed this quote."
   - DW AI shows downstream effect: site survey creation, job eligibility, lock status.

Initial action scope:

- Summarize quote.
- Detect missing fields.
- Draft revision note.
- Create follow-up.
- Draft customer message.
- Recommend approval path.

Do not auto-authorize, auto-send, or auto-lock structural data without explicit confirmation.

## 8.4 Site Survey

Site Survey is a perfect voice/mobile AI use case because the engineer is on site, measuring, taking photos, and discovering changes.

High-value use cases:

1. Voice measurement capture.
   - "Living room window width 1210 height 1490. Glass type laminated. Add photo."
   - DW AI maps words into position measurement fields.

2. Change detection explanation.
   - "What changed from quotation?"
   - DW AI explains position increase/decrease/quantity/dimension changes.

3. Photo note generation.
   - "Add note: wall not plastered and sill uneven."
   - DW AI cleans the note and attaches it to the position.

4. Survey completion check.
   - "Can I close this survey?"
   - DW AI checks measurements, required photos, remarks, changed positions.

5. Design update handoff.
   - "Summarize survey changes for design team."
   - DW AI generates structured handoff.

6. Customer communication.
   - "Tell customer survey completed and design update required."
   - Draft only, confirm before send.

Initial action scope:

- Add measurement note.
- Add position-level remark.
- Summarize changes.
- Create design update task.
- Schedule follow-up.

Do not auto-close survey when required measurements/photos are missing.

## 8.5 Job Management

Job is the operational spine. The docs define it as the bridge from won quotation to procurement, warehouse issue, production, delivery, and invoicing. That makes it the best module for "what is blocking what?"

High-value use cases:

1. Job command center.
   - "What is blocking job JB-1042?"
   - DW AI checks payment milestones, material issue, PO status, production, delivery, invoices.

2. Task progress update.
   - "Set fabrication 60 percent complete."
   - "Mark design approval task done."
   - "Make production depend on advance payment."

3. Dependency update.
   - "Installation cannot start until dispatch is delivered."
   - "Block production until stock issue is complete."

4. Milestone gate explanation.
   - "Why can't production start?"
   - DW AI explains payment, material, or approval gate.

5. Job note capture.
   - "Add internal note: customer wants phased delivery."

6. Job closure check.
   - "Can this job be closed?"
   - DW AI checks all deliveries, payments, invoice settlement, status.

7. Manager daily job brief.
   - "Which jobs are at risk this week?"
   - "Which jobs are ready for dispatch?"

Initial action scope:

- Add job note.
- Create/update job task.
- Update task progress.
- Add dependency.
- Explain blockers.
- Create follow-up.

Do not auto-close jobs or bypass milestone gates.

## 8.6 Purchase Orders

PO has approval, GRN, supplier performance, partial receipt, stock impact, powder coating, and high-value thresholds. DW AI can reduce purchasing mistakes and delays.

High-value use cases:

1. PO draft from job BOM.
   - "Create PO draft for missing materials for JB-1042."
   - DW AI groups by supplier, checks usable stock, and prepares draft lines.

2. Reorder assistant.
   - "What should we reorder today?"
   - DW AI uses reorder point, current stock, lead time, and open POs.

3. Supplier comparison.
   - "Which supplier should we use for PRF-101?"
   - DW AI compares price, lead time, rejection rate, on-time delivery.

4. Approval explanation.
   - "Why does this PO need Level 2 approval?"

5. GRN assist.
   - "Record partial receipt: 80 of 100 profiles received, 2 damaged."
   - DW AI drafts GRN lines, stock impact, damage flag.

6. Overdue PO follow-up.
   - "Draft WhatsApp to supplier for overdue PO."

Initial action scope:

- Draft PO.
- Suggest reorder.
- Explain approval.
- Draft supplier follow-up.
- Draft GRN from typed/voice input.

Do not auto-submit PO, auto-approve PO, or auto-increase stock without confirmation.

## 8.7 Warehouse / Material / Stock

Stock is one of the highest-value AI areas because small data-entry delays create wrong purchasing and production decisions. The docs distinguish live stock and usable stock, stock adjustment, stock transactions, reorder alerts, material issue, returns, powder coating, returnable tools, and offcuts.

High-value use cases:

1. Stock answer.
   - "How much usable PRF-101 do we have in Ahmedabad?"
   - "Why is usable stock lower than live stock?"

2. Issue material by voice.
   - "Issue 5 lengths PRF-101 to job JB-1042 from rack A3."
   - DW AI previews stock impact and requires confirmation.

3. Return material.
   - "Return 2 damaged handles from JB-1042."
   - DW AI asks condition: reusable, damaged, scrap, spare.

4. Adjustment explanation.
   - "Physical count is 48, system says 50. Adjust and note counting variance."
   - Preview required. Large adjustment escalates.

5. Reorder and shortage.
   - "Which jobs will be blocked by low stock?"
   - "Create reorder suggestion for low-stock profiles."

6. Material master creation.
   - "Create material for black powder coated profile PRF-56101."
   - DW AI checks duplicate item code and required fields.

7. Offcut assistant.
   - "Do we have an offcut long enough for 840mm cut?"

8. Stock anomaly.
   - "Why did glass stock drop sharply yesterday?"
   - DW AI explains transactions by GRN, issue, adjustment, return.

Initial action scope:

- Ask stock questions.
- Draft issue/return/adjustment.
- Add stock note/reason.
- Suggest reorder.
- Explain stock movement.

Never let DW AI silently modify stock. All stock-changing actions need preview and confirmation.

## 8.8 Production

Production has barcode generation, stage-based tracking, scanning, worker identification, sequence enforcement, quality gates, photo capture, progress, and delivery-ready status. This is where voice + mobile + scan can be extremely useful.

High-value use cases:

1. Production progress update.
   - "Mark cutting complete for barcode 88421."
   - "Set assembly stage delayed because gasket missing."

2. Worker station assistant.
   - "What should I work on next?"
   - DW AI lists assigned barcodes by priority and due date.

3. Sequence enforcement explanation.
   - "Why can't I scan this at assembly?"
   - DW AI explains prior process not complete.

4. Quality defect capture.
   - "Fail this item. Scratch on outer frame. Add photo."
   - DW AI creates defect/rework record.

5. Daily production summary.
   - "Summarize today's output and blockers."
   - "Which stage is the bottleneck?"

6. Waste/offcut capture.
   - "Record 1.2 meter usable offcut after cut."
   - DW AI drafts offcut return to stock.

7. Production plan risk.
   - "Will we finish JB-1042 by Friday?"
   - DW AI checks remaining stages, worker capacity, blocked materials.

Initial action scope:

- Ask work queue.
- Mark stage completed with confirmation.
- Add delay reason.
- Draft defect/rework record.
- Summarize production status.

Do not let DW AI bypass stage sequence or mark final completion without required scan/check.

## 8.9 Dispatch / Delivery

Dispatch is fragile-goods logistics. The planned module has packaging, vehicle assignment, route planning, POD, damage reports, GPS/ETA, and loading sequence. DW AI can be valuable because delivery mistakes are costly.

High-value use cases:

1. Dispatch readiness.
   - "Which completed items are ready for dispatch?"
   - DW AI checks production completion, payment gate, packaging, delivery address.

2. Loading plan.
   - "Plan loading sequence for tomorrow's deliveries."
   - DW AI considers A-frame capacity, fragility, delivery sequence.

3. Vehicle recommendation.
   - "Which vehicle can handle this dispatch?"
   - DW AI checks weight, dimensions, A-frame slots, driver availability.

4. POD capture.
   - "Delivered to Mr. Shah, good condition, add signature and photos."
   - DW AI prepares POD record.

5. Damage report.
   - "One glass panel chipped on arrival."
   - DW AI creates damage report, links photos, triggers follow-up workflow.

6. Customer update.
   - "Send ETA update to customer."
   - Draft only; confirm send.

Initial action scope:

- Explain dispatch readiness.
- Draft dispatch order.
- Draft route/loading plan.
- Record delivery note/POD with confirmation.
- Draft damage report.

Do not auto-close delivery without POD/signature/photo where required.

## 8.10 Installation

Installation has scheduling, team assignment, site readiness, scaffolding, weather holds, equipment, progress, incidents, punch list, and customer sign-off. DW AI can help field teams update quickly and help managers see risk early.

High-value use cases:

1. Site readiness.
   - "Site ready except scaffolding not erected."
   - DW AI updates checklist and blocks installation start.

2. Team assignment.
   - "Who can install this curtain wall next week?"
   - DW AI checks team skill, certification, location, workload.

3. Weather hold.
   - "Put installation on weather hold due to high wind."
   - DW AI creates hold, reschedule suggestion, customer update draft.

4. Daily progress.
   - "Installed 8 panels today, 4 remaining, access issue on level 3."
   - DW AI creates progress log and risk note.

5. Snagging.
   - "Add snag: handle loose in master bedroom window."
   - DW AI adds punch list item.

6. Completion check.
   - "Can we close this installation?"
   - DW AI checks all positions, punch list, photos, sign-off, equipment return.

Initial action scope:

- Update checklist.
- Log daily progress.
- Create weather hold.
- Add snag/punch item.
- Draft customer update.

Do not auto-sign off installation. Customer sign-off stays manual.

## 8.11 Finance / Payment

Finance is high-risk but high-value. Docs define invoicing, payment receipts, proforma, payment milestones, credit notes, aging, tax, and external accounting exports. Finance AI should be conservative.

High-value use cases:

1. Payment blocker explanation.
   - "Which jobs are blocked by payment?"
   - "Why is production blocked?"

2. Invoice drafting.
   - "Create invoice draft for delivered positions on JB-1042."
   - DW AI prepares invoice lines from job/delivery data.

3. Payment receipt capture.
   - "Record 50,000 received against INV-1021 by bank transfer."
   - DW AI previews milestone clearing and balance.

4. Overdue collection.
   - "Which invoices should I chase first?"
   - DW AI prioritizes by amount, age, customer value, risk.

5. Reminder drafting.
   - "Draft payment reminder for 30-day overdue invoice."

6. Credit note assist.
   - "Prepare credit note because one panel was damaged."
   - Manager approval required.

7. Cash view.
   - "How much cash is expected this month?"

Initial action scope:

- Explain AR and blockers.
- Draft invoice.
- Draft payment reminder.
- Draft receipt.
- Match payment to milestone suggestion.

Do not finalize invoices, release retention, approve credit notes, or mark paid without confirmation and role check.

## 8.12 Reporting / Owner Dashboard

Reporting is where DW AI can become the owner's daily operating system.

High-value use cases:

1. Owner morning brief.
   - "Tell me today's factory status."
   - Output: revenue, overdue, stock alerts, production delays, dispatch, installation, finance risk.

2. Natural language reports.
   - "Show revenue this month by client."
   - "Which product type creates most defects?"
   - "Which supplier is causing delays?"

3. Exception summary.
   - "What changed since yesterday?"
   - "What needs my approval?"
   - "What will delay dispatch this week?"

4. Root cause summaries.
   - "Why are jobs delayed this month?"
   - DW AI groups causes: payment, stock, PO delay, production bottleneck, site not ready.

5. Proactive alerts.
   - "Three jobs may miss delivery date."
   - "Profile stock will stop two jobs in five days."
   - "Supplier A rejection rate is rising."

Initial action scope:

- Answer report questions.
- Generate summaries.
- Create alert.
- Draft daily/weekly report.

Do not change business data from reporting view unless redirected to underlying module action.

## 8.13 Admin / Settings / Masters

Admin is where AI can save setup time but must be tightly controlled.

High-value use cases:

1. Setup assistant.
   - "Create default departments for a fenestration company."
   - "Create approval flow: PO above 5 lakh needs director approval."

2. Master cleanup.
   - "Find duplicate material colors."
   - "Which masters are unused?"

3. Permission explanation.
   - "Why can't Priya approve this PO?"
   - DW AI explains role/permission/threshold.

4. Template drafting.
   - "Create WhatsApp template for survey scheduled."
   - "Create invoice reminder email."

5. Country setup.
   - "Set UAE VAT and AED as base currency."
   - High-impact action; confirmation and warning required.

Initial action scope:

- Explain permissions.
- Draft templates.
- Suggest master cleanup.
- Draft approval flow.

Do not change country, tax, currency, approval thresholds, or permissions without admin confirmation and impact warning.

## 9. Cross-Module "Huge" Use Cases

These are the strongest product-level stories for Manu/Mehul/customer demos.

### 9.1 From Lead To Job Without Re-Keying

User says:

"Create enquiry for Gulf Facade. Curtain wall project in Dubai, estimated 2 crore, need quote next week."

DW AI:

- Creates enquiry draft.
- Checks duplicate organization.
- Suggests sales rep/estimator.
- Creates follow-up.
- Prepares missing-data checklist.

Time saved: sales admin does not open four screens.

### 9.2 Job Blocker Brain

User asks:

"Why is Job 1042 delayed?"

DW AI checks:

- Payment milestone.
- PO status.
- GRN.
- Stock availability.
- Material issue.
- Production scans.
- Dispatch readiness.
- Installation schedule.

Answer:

"Production is blocked because PRF-101 has 40m usable stock against 65m required. PO-221 is partially received; expected balance tomorrow. Advance payment is cleared."

This is a killer ERP use case.

### 9.3 Voice Factory Update

Worker says:

"Cutting complete for barcode 88421. Scratch found on profile 88422, send for rework."

DW AI:

- Updates completed item.
- Creates defect/rework record.
- Adds production note.
- Notifies supervisor if delay affects target date.

### 9.4 Site Survey Assistant

Survey engineer says:

"Bedroom window has changed. Width 1210 height 1490, quantity two. Customer wants frosted glass."

DW AI:

- Updates survey measurement draft.
- Flags quotation/design change.
- Adds customer preference note.
- Asks for photo if required.

### 9.5 Stock Shortage Prevention

Manager asks:

"Will any job stop this week because of stock?"

DW AI:

- Compares job BOM, usable stock, open POs, expected GRNs.
- Lists at-risk jobs.
- Suggests reorder/transfer/priority allocation.

### 9.6 Payment Gate Assistant

Manager asks:

"Which jobs can move forward if we collect payment today?"

DW AI:

- Lists jobs blocked by milestones.
- Shows amount due.
- Drafts WhatsApp reminders.
- Prioritizes by delivery date and value.

### 9.7 Delivery And Installation Risk

Manager asks:

"What can fail tomorrow?"

DW AI:

- Checks dispatches, vehicle capacity, POD pending, weather, installation readiness, scaffolding, team assignment.
- Shows risk items and suggested actions.

### 9.8 Owner Weekly War Room

Owner asks:

"Give me weekly review."

DW AI:

- Sales pipeline.
- Quotations sent/won/lost.
- Jobs delayed.
- Production throughput.
- Stock shortage.
- Delivery performance.
- Overdue receivables.
- Top 5 actions for next week.

This becomes the executive layer of DWERP.

## 10. What DW AI Should Not Do Initially

To keep trust high, avoid these in V1:

- Auto-approve PO.
- Auto-finalize invoice.
- Auto-create credit note.
- Auto-close job.
- Auto-close installation.
- Auto-send customer messages without preview.
- Auto-adjust stock without confirmation.
- Auto-ignore payment gate.
- Auto-bypass production sequence.
- Auto-delete or overwrite master data.

These can become assisted workflows later, but V1 should stay conservative.

## 11. Suggested Product Packaging

DW AI should not be a single feature. Package it as a layer.

### Package 1 - DW AI Assistant

Included in base system.

- Ask questions.
- Navigate.
- Summarize records.
- Explain blockers.
- Draft notes.

### Package 2 - DW AI Actions

Paid add-on or higher plan.

- Update tasks/progress.
- Create follow-ups.
- Draft PO/invoice/survey changes.
- Guided stock movement drafts.
- Approval assistant.

### Package 3 - DW AI Operations Intelligence

Premium plan.

- Owner morning brief.
- Production bottleneck detection.
- Stock shortage forecast.
- Payment risk.
- Supplier performance.
- Defect pattern detection.
- Weekly executive report.

### Package 4 - DW AI Voice / Mobile Floor Assistant

Premium operations add-on.

- Factory voice updates.
- Site survey voice notes.
- Installation voice progress.
- Dispatch POD voice assist.

## 12. MVP Roadmap

### Phase A - Read And Explain

Goal: DW AI becomes useful without changing data.

Build:

- Global ask/search.
- Record summary.
- "Why blocked?"
- "What changed?"
- "What needs my attention?"

Modules:

- CRM.
- Jobs.
- Stock.
- Finance.

### Phase B - Draft And Confirm

Goal: DW AI prepares work, user applies.

Build:

- Follow-up creation.
- Notes.
- Task/progress update.
- Status change draft.
- WhatsApp/email draft.
- Stock movement draft.
- PO draft.
- Invoice reminder draft.

### Phase C - Voice/Mobile Updates

Goal: field and factory workers can update ERP without desktop forms.

Build:

- Push-to-talk command.
- Measurement capture.
- Production status update.
- Dispatch/POD note.
- Installation progress note.

### Phase D - Proactive Operations

Goal: DW AI becomes operational radar.

Build:

- Daily brief.
- Risk alerts.
- Reorder suggestions.
- Payment gate reminders.
- Supplier/quality risk.
- Production bottleneck.

## 13. Success Metrics

Track whether DW AI is actually saving time.

Operational metrics:

- Follow-ups created by AI.
- Notes captured by voice.
- Task/progress updates via AI.
- Average time to update job status.
- Reduction in stale enquiries.
- Reduction in overdue follow-ups.
- Reduction in missing survey fields.
- Stock adjustments with complete reasons.
- Jobs with blocker reason recorded.
- Payment reminders sent on time.

Business metrics:

- Lead-to-quote time.
- Quote-to-job conversion time.
- Job delay days.
- Production bottleneck time.
- Stockout incidents.
- Dispatch delay count.
- Overdue receivable amount.
- Owner time spent finding answers.

Trust metrics:

- AI suggestions accepted.
- AI suggestions edited before apply.
- AI suggestions cancelled.
- Wrong entity matches.
- Actions blocked by permissions.
- Manual overrides after AI action.

## 14. User Experience Rules

1. Always show what DW AI is about to change.
2. Always show the matched record.
3. Always show confidence if there is ambiguity.
4. Always ask when multiple records match.
5. Never hide the original transcript.
6. Never apply stock/money/legal/customer-facing action silently.
7. Always write audit trail: user, transcript, parsed action, before, after.
8. Always let user edit before confirm.
9. Always explain blocked actions in plain language.
10. Always provide a fallback button: "Open full form."

## 15. Example DW AI Prompts By Role

### Owner / Director

- "What is blocking revenue this week?"
- "Which jobs are delayed and why?"
- "Which invoices should I chase first?"
- "Which stock shortage can stop production?"
- "Give me yesterday's factory summary."

### Sales

- "Create follow-up for this customer tomorrow."
- "Summarize this enquiry before I call."
- "Draft WhatsApp for revised quotation."
- "Which leads are going cold?"

### Estimator

- "What information is missing before I quote?"
- "Summarize site survey changes."
- "Create revision note for changed glass spec."

### Warehouse

- "Issue material to job 1042."
- "Return unused profiles from job 1042."
- "Why is usable stock less than live stock?"
- "Which items need reorder?"

### Production

- "What should this station work on next?"
- "Mark cutting complete for barcode 88421."
- "Report defect: corner crack, send to rework."
- "Summarize today's production delay."

### Dispatch

- "Which jobs are ready for dispatch?"
- "Create POD note for this delivery."
- "Report delivery damage."
- "Draft customer ETA update."

### Installation

- "Site not ready, scaffolding missing."
- "Installed 8 panels today, 4 remaining."
- "Put job on weather hold."
- "Add snagging item."

### Finance

- "Which jobs are blocked by payment?"
- "Draft payment reminder for overdue invoice."
- "Record payment receipt draft."
- "What is expected cash collection this week?"

## 16. Recommended Starting Point

Start with one unified concept:

> DW AI Work Update

This is the smallest useful product:

- User types or speaks.
- DW AI understands record + intent.
- It previews the update.
- User confirms.
- ERP updates and logs it.

Start with four action types:

1. Add note.
2. Create/complete follow-up.
3. Update task progress/status.
4. Explain blocker.

Then expand to:

5. Draft customer message.
6. Draft stock movement.
7. Draft PO/invoice.
8. Daily manager brief.

This approach gives immediate value without making risky promises.

## 17. Demo Narrative For Manu / Customer

The best demo should not show "AI chat answering random questions." It should show DW AI controlling real ERP work with previews and guardrails.

### Demo 1 - Sales To Job Handoff

1. User says: "Create enquiry for Gulf Facade, curtain wall, Dubai, estimated 2 crore."
2. DW AI creates a draft enquiry and warns if a duplicate organization exists.
3. User confirms.
4. User says: "Create follow-up for tomorrow and assign estimator."
5. DW AI prepares follow-up and estimator assignment.
6. Manager asks: "What changed today?"
7. DW AI reports the new enquiry, assigned estimator, and pending quote work.

Message: sales admin time drops because users can capture work in natural language.

### Demo 2 - Job Blocker Brain

1. Manager asks: "Why is Job 1042 delayed?"
2. DW AI checks payment milestone, stock, PO, GRN, material issue, production, and delivery.
3. DW AI replies: "Production is blocked by PRF-101 shortage. PO-221 is partially received. Advance payment is cleared."
4. Manager asks: "Draft action plan."
5. DW AI suggests stock transfer, supplier follow-up, and revised production date.

Message: the owner gets one answer instead of opening six modules.

### Demo 3 - Factory Voice Update

1. Worker says: "Cutting complete for barcode 88421. Barcode 88422 has scratch, send to rework."
2. DW AI shows two changes: complete one item, create defect/rework for another.
3. Worker confirms.
4. Supervisor sees production progress and defect count updated.

Message: factory floor updates happen at the point of work.

### Demo 4 - Finance Gate

1. Manager asks: "Which jobs can move if payment arrives today?"
2. DW AI lists jobs blocked by payment milestone.
3. User says: "Draft WhatsApp reminder for the top two."
4. DW AI drafts messages but does not send until confirmed.

Message: finance becomes an operating gate, not a disconnected report.

## 18. DW AI Action Catalogue Starter

This is the starter action catalogue. A full implementation catalogue should expand each row into allowed roles, required fields, validation rules, affected tables, audit entry, and rollback/failure behavior.

| Action | Module | User command | DW AI result | Confirmation level |
|---|---|---|---|---|
| Add note | All modules | "Add note..." | Clean note attached to current record | Preview |
| Create follow-up | CRM, Quote, Job, Finance | "Remind me..." | Follow-up draft with date/time/owner | Preview |
| Complete follow-up | CRM, Quote, Job | "Mark follow-up done..." | Follow-up closed with outcome note | Preview |
| Explain blocker | Job, Stock, Production, Finance | "Why blocked?" | Plain-language reason with source records | None |
| Update task progress | Job, Production, Installation | "Set task to 60%" | Task progress update | Preview |
| Add dependency | Job, Production, Delivery, Installation | "Make X depend on Y" | Dependency draft and impact note | Preview plus warning |
| Draft WhatsApp | CRM, Quote, Survey, Finance | "Draft message..." | Message draft using record context | Edit/send confirmation |
| Draft stock issue | Stock/Warehouse | "Issue material..." | Material issue draft with stock impact | Strong confirmation |
| Draft stock return | Stock/Warehouse | "Return material..." | Return draft with condition bucket | Strong confirmation |
| Draft PO | PO | "Create PO draft..." | PO header/lines from BOM/reorder need | Strong confirmation |
| Draft GRN | PO/Stock | "Record receipt..." | GRN draft with stock impact | Strong confirmation |
| Draft invoice | Finance | "Create invoice draft..." | Invoice draft from job/delivery data | Strong confirmation |
| Record payment draft | Finance | "Record payment..." | Receipt draft and milestone impact | Strong confirmation |
| Site survey update | Survey | "Width 1210 height 1490..." | Measurement draft linked to position | Preview |
| Production defect | Production | "Fail item, scratch..." | Defect/rework draft | Preview |
| Dispatch POD draft | Delivery | "Delivered to..." | POD draft with condition/photo/signature prompts | Strong confirmation |
| Installation progress | Installation | "Installed 8 panels..." | Daily progress log | Preview |
| Owner daily brief | Reporting | "What needs attention?" | Cross-module exception summary | None |

This catalogue should become the product control sheet for DW AI. No new AI action should be built unless it has a row in this table.

## 19. Final Recommendation

DW AI should become the operating assistant across DWERP, not just a chat panel.

The strongest message:

> DWERP is not only a system where users enter data. DWERP is a system where users tell the ERP what happened, and the ERP keeps the business updated in real time.

The first product story should be:

> "A factory worker, survey engineer, salesperson, or manager can update the ERP in seconds by voice or text, with DW AI validating the action and the ERP keeping the audit trail."

That is a real differentiator for manufacturing ERP, especially in fenestration where work moves across office, warehouse, factory, delivery, and site.

The best next artifact would be a full `DW AI Action Catalogue` with every allowed action, required permission, confirmation level, affected module, audit rule, and user-facing confirmation copy.







# DW AI — What Is Built vs. What Needs to Be Built

> Prepared: 2026-05-19  
> Based on: `DWERP-Agent/ARCHITECTURE.md` (technical reality) + `DW-AI-ERP-USE-CASES-REPORT-2026-05-18.md` (product vision)  
> Purpose: Give a clear, single-page picture of what exists today and what needs to be implemented next.

---

## Quick Summary

| | Status |
|---|---|
| DW AI can **read** ERP data and answer questions | ✅ Built |
| DW AI can **explain** data with charts and KPIs | ✅ Built |
| DW AI can **write** anything to the ERP | ❌ Not built |
| DW AI respects **user permissions** before acting | ❌ Not built |
| DW AI sends **proactive alerts** without being asked | ❌ Not built |
| DW AI works as a **module-specific assistant** (on Job page, Quotation page, etc.) | ❌ Not built |
| DW AI gives a **manager morning brief** automatically | ❌ Not built |
| DW AI works as a full **voice/mobile update tool** for field workers | ❌ Not built |

**The core gap in one sentence:**  
> DW AI today is a smart READ-ONLY data assistant. The product vision wants it to be a full ERP operating assistant that can also WRITE, DRAFT, and ACT — with permission checks and confirmation before every change.

---

## Part 1 — What Is Currently Built

### 1.1 Core Chat Engine

| Component | What it does | Status |
|---|---|---|
| FastAPI + LangGraph ReAct loop | Processes user messages, runs tools iteratively, streams answers | ✅ Done |
| SSE streaming | Sends answer chunks to frontend in real time (text, chart, KPI, table) | ✅ Done |
| Claude Haiku 4.5 (via OpenRouter) | Primary LLM for all queries | ✅ Done |
| 3-model tier routing | Routes complex/medium/simple queries to different models | ✅ Built, currently disabled (FORCE_TIER1=True) |
| Conversational fast-path | "Hi / thanks / how are you" answered in <100ms, skips all RAG | ✅ Done |
| LLM fallback chain | If OpenRouter is down → direct Anthropic → local Ollama | ✅ Done |

### 1.2 SQL Query Tool (Read-Only)

| Component | What it does | Status |
|---|---|---|
| `dwerp_sql_query` tool | LLM generates SQL SELECT → tool validates → executes → returns rows | ✅ Done |
| 4-layer SQL security | Blocks any non-SELECT, blocks DML, enforces tenant isolation, masks PII | ✅ Done |
| Error retry | On SQL failure, agent retries once with corrected SQL | ✅ Done |
| Soft-delete filters | All queries auto-require `deleted_at IS NULL` validation | ✅ Done |
| GenericForeignKey joins | Agent knows how to join follow-ups, status history via Django GFK pattern | ✅ Done |

**Critical limitation:** The SQL tool can only run SELECT queries. There is no tool that can INSERT, UPDATE, or DELETE anything in the ERP. Writing to the database is completely blocked.

### 1.3 RAG — Document Search

| Component | What it does | Status |
|---|---|---|
| Document upload | Users can upload PDF, DOCX, XLSX files | ✅ Done |
| Chunking + embedding | Splits documents, embeds with Gemini Embedding 2, stores in pgvector | ✅ Done |
| Hybrid search | Dense vector (pgvector) + BM25 full-text + BGE reranker | ✅ Done |
| Schema RAG | From 43 CRM tables, picks top 10 relevant ones per query (saves tokens) | ✅ Done |
| Quick Facts search | Keyword search on learned connector knowledge facts | ✅ Done |

### 1.4 3-Tier Memory System

| Tier | What it stores | Status |
|---|---|---|
| Tier 1 — Query Memory | Every successful NL→SQL pair, recalled as few-shot examples for similar future questions | ✅ Done |
| Tier 2 — Error Memory | Every failed SQL with error message, injected as "DO NOT do this" patterns | ✅ Done |
| Tier 3 — Connector Knowledge | Enum values, column notes, join patterns learned about the CRM | ✅ Done |
| User feedback loop | User says "that was wrong" → stores corrected SQL | ❌ Not built |

### 1.5 Personal Memory & Onboarding

| Component | What it does | Status |
|---|---|---|
| 5-step onboarding | Collects user name, topics, format preference, agent name, update preference | ✅ Done |
| Natural language preference updates | "Call me Raj" → updates display_name without LLM | ✅ Done |
| Proactive preference stored | User can set morning/evening/alerts preference | ✅ Stored, NOT acted on |

### 1.6 Security & Multi-Tenancy

| Component | Status |
|---|---|
| JWT auth (own tokens + Supabase tokens) | ✅ Done |
| Tenant data isolation (5 layers: JWT, context var, SQL param, memory scoping, override validation) | ✅ Done |
| Prompt injection defense | ✅ Done |
| PII masking (salary, Aadhaar, PAN, bank accounts, etc.) | ✅ Done |
| RBAC — role-based permission checks | ❌ Schema exists, NOT wired. All users treated as "authenticated" (same role) |

### 1.7 Caching & Performance

| Component | Status |
|---|---|
| Redis exact cache (TTL 300s) | ✅ Done |
| Semantic cache (cosine similarity ~0.95) | ✅ Done |
| Per-query RAG cache (avoids re-running schema RAG on each ReAct iteration) | ✅ Done |

### 1.8 API Endpoints

| Endpoint | Purpose | Status |
|---|---|---|
| `POST /api/chat` | Main chat with SSE streaming | ✅ Done |
| `GET /api/chat_history` | Fetch past conversations | ✅ Done |
| `POST /api/upload` | Upload PDF/DOCX/XLSX for RAG | ✅ Done |
| `POST /api/voice` | Voice input → transcription | ✅ Endpoint exists |
| `GET /api/connectors` | List available connectors | ✅ Done |
| Admin endpoints (memory, error, audit stats) | Monitoring | ✅ Done |
| Write action execution endpoints | Actually apply changes to ERP | ❌ Not built |

### 1.9 What the Frontend Already Has

- SSE consumer that renders text, charts, KPIs, tables from streaming chunks
- Confirmation-card pattern (the UI structure for showing a preview before applying) — built, but no backend action flows into it yet
- Voice command palette that routes unmatched text to DW AI chat

---

## Part 2 — What Needs to Be Implemented

The product vision defines **5 capability levels**. Currently only Levels 1 and 2 are working.

| Level | Name | Current Status |
|---|---|---|
| Level 1 | Ask (read ERP data, answer questions) | ✅ Working |
| Level 2 | Explain (why is something blocked/delayed) | ⚠️ Partial — can query data but no cross-module reasoning engine |
| Level 3 | Draft (prepare an action, don't apply it) | ❌ Not built |
| Level 4 | Update (write to ERP after preview + confirmation) | ❌ Not built |
| Level 5 | Recommend & Automate (proactive alerts, workflow triggers) | ❌ Not built |

---

### 2.1 The Most Critical Missing Piece — Write Actions

**What the architecture says:**  
The `propose_action` tool exists in the LangGraph agent's tool list as a comment/placeholder, and the SSE stream has an `action` chunk type defined. But the backend logic to actually execute a write action against the ERP is not built. The frontend confirmation card also has no API to call when the user clicks "Confirm."

**What needs to be built:**

#### A. `propose_action` Tool (backend)
A new LangGraph tool (alongside `dwerp_sql_query`) that:
1. Takes action type + parameters from LLM (e.g., `create_follow_up`, `update_task_progress`, `add_note`)
2. Validates: user has permission for this action (needs RBAC)
3. Returns a structured preview: what will change, on which record, before/after values
4. Streams it to frontend as `action` type SSE chunk

#### B. Action Execution Endpoint (backend)
```
POST /api/action/confirm
Body: { action_id, confirmed: true/false, edits: {} }
```
- Looks up the proposed action
- Re-validates permissions
- Calls the appropriate DWERP backend API endpoint (REST/DB write)
- Logs audit trail: user, timestamp, transcript, before state, after state
- Returns success/failure

#### C. Action Confirmation UI (frontend)
The confirmation card already exists structurally. It needs to:
- Render the action preview (record name, field, before → after)
- Provide an Edit button (user can modify before confirming)
- Provide Confirm and Cancel buttons
- Call `POST /api/action/confirm` on confirm
- Show result inline

---

### 2.2 RBAC — Permission-Aware Actions

**Current state:** All authenticated users get the same role. DW AI does not check if a user actually has permission to do what they asked.

**What needs to be built:**
- Wire `msbc_rbac.accounts.UserRole` into the auth flow
- Create a permission check layer that runs BEFORE proposing any action
- Define action classes with who can confirm them:

| Action Class | Who Can Confirm |
|---|---|
| Add internal note | Any user with module edit access |
| Create / complete follow-up | Assigned user |
| Update task progress | Job/project owner or assigned worker |
| Change enquiry/quotation status | Module owner or allowed role |
| Issue / return / adjust stock | Warehouse authorized role only |
| Approve / reject PO | Finance / manager approver |
| Send WhatsApp / email | Record owner or approved role |
| Finalize invoice, credit note | Finance / manager only |

- If the user does NOT have permission, DW AI must explain it in plain language ("You don't have stock issue permission. Contact your warehouse manager.")

---

### 2.3 The First 10 Write Actions to Build (Priority Order)

These are low-to-medium risk, high-frequency, and immediately demonstrate value:

| # | Action | Example Command | Confirmation Type | Risk Level |
|---|---|---|---|---|
| 1 | Add internal note | "Add note: customer wants black hardware." | Apply preview | Low |
| 2 | Create follow-up | "Remind me to call Ali tomorrow at 11." | Apply preview | Low |
| 3 | Complete follow-up | "Mark this follow-up done; customer asked for revision." | Apply preview | Low |
| 4 | Explain blocker (cross-module) | "Why is this job blocked?" | None (read-only) | None |
| 5 | Update task progress | "Set cutting to 60 percent." | Apply preview | Medium |
| 6 | Add dependency/blocker | "Installation depends on delivery." | Apply preview | Medium |
| 7 | Draft WhatsApp message | "Draft quote follow-up message." | Edit + send confirm | Medium |
| 8 | Draft stock issue | "Issue 5 PRF-101 to job 1042." | Strong confirmation | High |
| 9 | Draft payment reminder | "Draft reminder for overdue invoice 1021." | Edit + send confirm | Medium |
| 10 | Daily manager brief | "What needs attention today?" | None (read-only) | None |

**Do not build yet:** auto-approve PO, auto-finalize invoice, auto-adjust stock, auto-send customer messages, auto-close job or installation. These require more trust to be established first.

---

### 2.4 Improved "Explain Blocker" (Cross-Module Reasoning)

Currently DW AI can only answer questions by running SQL against one or two tables. The "why is this job blocked?" use case requires checking across 6–8 modules in one query chain.

**What needs to be built:**  
A dedicated cross-module blocker analysis flow:

```
User: "Why is Job JB-1042 blocked?"
  │
  ├── Check payment milestone status
  ├── Check stock availability vs. job BOM
  ├── Check open POs and expected GRN dates  
  ├── Check material issue status
  ├── Check production stage completion
  ├── Check dispatch readiness
  └── Synthesize: "Production is blocked by PRF-101 shortage. 
                    PO-221 partially received. Advance payment cleared."
```

This is currently only possible as a chain of SQL queries the LLM might figure out in 4–5 ReAct iterations. A structured blocker reasoning tool would be faster and more reliable.

---

### 2.5 Module-Specific AI Panel

**Current state:** DW AI is a global chat sidebar. It does not know which record the user is currently viewing.

**What needs to be built:**
- Each major ERP page (Job, Quotation, Enquiry, Stock, etc.) passes its current record ID + type to the DW AI panel via URL or context
- The system prompt is injected with: "The user is currently viewing Job JB-1042. Here is its current state: [record summary]."
- DW AI can then answer module-specific questions WITHOUT the user needing to type the job number every time
- Write actions are scoped to the current record by default

**Pages that need this panel (priority order):**
1. Job page
2. Quotation page
3. Enquiry page
4. Stock / Material page
5. Purchase Order page
6. Site Survey page

---

### 2.6 Proactive Alerts & Manager Morning Brief

**Current state:** The `proactive_preference` field is stored per user (morning / evening / alerts) but nothing actually sends proactive messages. There is no background job.

**What needs to be built:**

#### A. Background Job Runner
- Add Celery or RQ (Redis Queue) to the Docker stack
- Cron job triggers at configured times (e.g., 8:00 AM)

#### B. Morning Brief Generator
Runs these queries automatically and compiles results:
- Which jobs are blocked today (payment, stock, PO)
- Which follow-ups are overdue or due today
- Which stock items are at or below reorder point
- Which POs are overdue from supplier
- Which dispatches are delayed
- Which invoices are overdue (30+ days)
- What needs approval (POs, quotations, stock adjustments)

Delivers as:
- A pre-filled chat message when the user opens DW AI
- OR a push notification (future)

#### C. Alert Types
| Alert | Trigger Condition |
|---|---|
| Stock shortage warning | Usable stock < reorder level |
| Job at risk | Job due in 3 days with open blockers |
| Overdue follow-up | Follow-up missed and not rescheduled |
| Payment gate | PO/production blocked, advance payment pending |
| Overdue supplier PO | Supplier delivery date passed, no GRN |
| Overdue invoice | Invoice > 30 days unpaid |
| Production bottleneck | One stage has > 5 items pending > 2 days |

---

### 2.7 Voice / Mobile Quick Update Flow

**Current state:** The `/api/voice` endpoint exists (transcription). The frontend `VoiceCommandPalette` routes unmatched speech to DW AI chat. But the full voice-to-action flow (speak → preview → confirm → ERP updated) is not implemented end to end.

**What needs to be built:**

1. **Voice transcription → action detection:** After transcription, the LLM must detect whether the user is asking a question or requesting an action. Currently it just treats it as a chat message.

2. **Action preview via voice:** If an action is detected, show the preview card. User can say "confirm" or "cancel" instead of tapping.

3. **Mobile-optimized interface:** 
   - Large confirm/cancel buttons for factory floor use
   - Push-to-talk button prominent
   - Screen dimming after idle to save battery
   - Works even with gloves (large touch targets)

4. **Role-specific voice shortcuts:** 
   - Worker: "Mark cutting complete for barcode [scan/speak]"
   - Warehouse: "Issue [qty] [material] to job [number]"
   - Survey engineer: "Width [X] height [Y] for [position name]"
   - Installation: "Weather hold. Resume [date]."

---

### 2.8 Module-by-Module Gaps

#### CRM / Enquiry
| Use Case | Status |
|---|---|
| Query enquiry data (count, status, pipeline) | ✅ Works |
| "Which leads need follow-up?" | ✅ Works |
| Create enquiry from natural language | ❌ Not built |
| Detect duplicate customer on create | ❌ Not built |
| Create / complete follow-up | ❌ Not built |
| Move enquiry status with reason | ❌ Not built |
| Draft WhatsApp message | ❌ Not built |

#### Quotation
| Use Case | Status |
|---|---|
| Summarize quotation | ✅ Works (read SQL) |
| "What is pipeline value?" | ✅ Works |
| Detect missing fields before sending | ❌ Not built |
| Create revision note | ❌ Not built |
| Recommend approval path | ❌ Not built |
| Lock/send quotation | ❌ Not built (high risk, needs confirmation) |

#### Site Survey
| Use Case | Status |
|---|---|
| Query survey status | ✅ Works |
| Voice measurement capture | ❌ Not built |
| Detect changes from quotation | ❌ Not built |
| Add position remark from voice | ❌ Not built |
| Survey completion check | ❌ Not built |

#### Job Management
| Use Case | Status |
|---|---|
| "Which jobs are blocked?" | ✅ Works |
| "What is job JB-1042 status?" | ✅ Works |
| Cross-module blocker explanation | ⚠️ Partial (takes many iterations) |
| Update task progress | ❌ Not built |
| Add task dependency | ❌ Not built |
| Add internal job note | ❌ Not built |
| Job closure check | ❌ Not built |

#### Purchase Orders
| Use Case | Status |
|---|---|
| "Which POs are overdue?" | ✅ Works |
| Draft PO from job BOM | ❌ Not built |
| Supplier comparison | ⚠️ Partial (can query supplier data) |
| Approval explanation | ⚠️ Partial (can read approval status) |
| Draft GRN from voice/typed input | ❌ Not built |
| Draft WhatsApp to overdue supplier | ❌ Not built |

#### Warehouse / Stock
| Use Case | Status |
|---|---|
| "How much stock does PRF-101 have?" | ✅ Works |
| "Why is usable stock less than live stock?" | ⚠️ Partial (can explain concept, limited transaction query) |
| Draft stock issue to job | ❌ Not built |
| Draft stock return | ❌ Not built |
| Draft stock adjustment with reason | ❌ Not built |
| Reorder suggestion | ⚠️ Partial (can flag low stock, can't create PO) |
| "Which jobs will stock block?" | ❌ Not built (needs BOM vs. stock comparison) |

#### Production
| Use Case | Status |
|---|---|
| "What is production status?" | ⚠️ Partial (Jobs table has 0 rows currently) |
| Mark stage complete (barcode) | ❌ Not built |
| Create defect/rework record | ❌ Not built |
| "What should this station work on next?" | ❌ Not built |
| Production bottleneck detection | ❌ Not built |

#### Dispatch / Delivery
| Use Case | Status |
|---|---|
| Dispatch readiness check | ❌ Not built |
| Create POD record | ❌ Not built |
| Damage report draft | ❌ Not built |
| Draft customer ETA update | ❌ Not built |

#### Installation
| Use Case | Status |
|---|---|
| Weather hold creation | ❌ Not built |
| Daily progress log | ❌ Not built |
| Add snagging item | ❌ Not built |
| Completion check | ❌ Not built |

#### Finance
| Use Case | Status |
|---|---|
| "Which invoices are overdue?" | ✅ Works |
| "Which jobs are blocked by payment?" | ✅ Works |
| Draft invoice from delivered positions | ❌ Not built |
| Draft payment reminder | ❌ Not built (can draft text, can't save) |
| Draft receipt capture | ❌ Not built |
| Cash flow forecast | ⚠️ Partial (can query receivables) |

#### Reporting / Owner Dashboard
| Use Case | Status |
|---|---|
| Answer report questions | ✅ Works |
| Generate natural language reports | ✅ Works |
| Automated morning brief | ❌ Not built |
| Proactive risk alerts | ❌ Not built |
| Owner weekly war room summary | ⚠️ Partial (user must ask; not automated) |

---

## Part 3 — Implementation Roadmap

### Phase A — Read & Explain (Current State, Needs Polish)

**Goal:** DW AI is reliably useful as a read-only assistant.

What to complete or improve:
- [ ] Fix cross-module blocker reasoning (structured tool for "why is X blocked?")
- [ ] Add jobs/production/dispatch/installation data to schema RAG (currently 0 rows in jobs table)
- [ ] Wire user feedback loop ("that answer was wrong" → store corrected SQL)
- [ ] Re-enable multi-tier routing optionally for cost savings on simple queries
- [ ] Improve proactive preference: actually trigger a morning brief summary on first chat open

---

### Phase B — Draft & Confirm (The Big Step)

**Goal:** DW AI can prepare any action. User reviews and confirms. ERP gets updated.

What to build:
- [ ] `propose_action` tool wired end-to-end (LLM → preview → SSE `action` chunk → frontend card)
- [ ] `POST /api/action/confirm` endpoint
- [ ] Frontend confirmation card wired to `/api/action/confirm`
- [ ] RBAC permission check before any action is proposed
- [ ] Audit trail: every AI-assisted write logged with before/after state
- [ ] First 10 write actions (see Section 2.3 above)
- [ ] Module-specific AI panel (current record context injected)

---

### Phase C — Voice & Mobile Updates

**Goal:** Field workers and factory floor workers can update ERP by speaking.

What to build:
- [ ] Voice → action detection (not just text passthrough)
- [ ] Action preview confirmation via voice ("say confirm or cancel")
- [ ] Mobile-optimized action confirmation UI
- [ ] Role-specific voice command shortcuts
- [ ] Barcode scan → production stage update flow

---

### Phase D — Proactive Operations Intelligence

**Goal:** DW AI watches the ERP in background and warns before problems happen.

What to build:
- [ ] Celery / RQ background job runner in Docker stack
- [ ] Morning brief generator (cron-triggered, delivers on chat open)
- [ ] Alert engine (stock shortage, overdue PO, payment gate, job at risk)
- [ ] Proactive suggestion feed (not just reactive answers)
- [ ] Owner weekly war room (auto-generated Monday morning)

---

## Part 4 — Key Technical Decisions Needed

| Decision | Options | Notes |
|---|---|---|
| How to execute write actions | Call DWERP backend REST API OR direct DB write | REST API is safer (goes through existing validation); direct DB bypasses it |
| RBAC integration | Query `msbc_rbac.accounts.UserRole` at action-propose time | Must be done before ANY write action ships |
| Background jobs | Celery + Redis (already have Redis) or RQ (simpler) | Celery is more production-grade; RQ is simpler to start |
| WhatsApp integration | DWERP already has WhatsApp Business API planned | DW AI should draft + let DWERP send (not directly call WhatsApp API) |
| Mobile app vs. mobile web | Mobile web (PWA) first for voice/mobile update | Native app is a later decision |
| Action audit table | Separate `ai_action_log` table vs. reuse existing audit trail | Separate table recommended (includes transcript, parsed intent, confidence) |

---

## Part 5 — What DW AI Should Never Do Automatically (No Exceptions)

These must always require human confirmation and cannot be triggered silently:

- Auto-approve any PO
- Auto-finalize or send any invoice
- Auto-create any credit note
- Auto-close any job or installation
- Auto-send any customer-facing message (WhatsApp, email)
- Auto-adjust stock without preview
- Auto-ignore any payment milestone gate
- Auto-bypass production stage sequence
- Auto-delete or overwrite any master data
- Auto-release retention or final payment

---

## Part 6 — Success Metrics to Track After Each Phase

### Phase B (Write Actions)
- Follow-ups created by DW AI per week
- Notes captured via AI vs. manually
- Task/progress updates via AI
- AI suggestions accepted vs. cancelled (acceptance rate target: > 70%)

### Phase C (Voice)
- Voice updates per day from factory/field users
- Average time to complete a job update (target: < 30 seconds)
- Voice command accuracy (target: < 5% misinterpretation rate)

### Phase D (Proactive)
- Morning brief open rate
- Alerts actioned within 1 hour
- Stock shortages caught before they block production
- Overdue follow-ups reduced vs. baseline

---

## Appendix — Files to Edit for Each Feature

| Feature to build | File to modify |
|---|---|
| Add write action tool | `backend/app/agent/tools.py` — add `propose_action()` |
| Action execution API | `backend/app/api/` — new router file `action.py` |
| RBAC permission check | `backend/app/core/security.py` + `backend/app/core/rbac.py` (new) |
| Cross-module blocker tool | `backend/app/agent/tools.py` — add `explain_blocker()` |
| Morning brief / proactive | New `backend/app/jobs/` directory + Celery config |
| Module-specific context | `backend/app/api/chat.py` — accept `record_type` + `record_id` in request body |
| Action audit trail | New migration + `backend/app/memory/action_log.py` |
| Frontend action card | Frontend confirmation card component → wire to `/api/action/confirm` |
| Voice → action detection | `backend/app/api/voice.py` — add intent classification after transcription |
