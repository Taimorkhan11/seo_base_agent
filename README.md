# AI Visibility Intelligence API

A RESTful Flask API that discovers high-value queries in a business's competitive space,
scores domain visibility in AI-generated answers, and generates actionable content
recommendations — powered by a three-agent Claude pipeline and real keyword data from DataForSEO.

---

## Table of Contents

1. [Quick Start (< 5 min)](#quick-start)
2. [Docker Setup](#docker-setup)
3. [API Reference](#api-reference)
4. [Architecture Decisions](#architecture-decisions)
5. [Agent Design Rationale](#agent-design-rationale)
6. [Opportunity Score Formula](#opportunity-score-formula)
7. [Data Model & Schema Decisions](#data-model--schema-decisions)
8. [Model Selection Rationale](#model-selection-rationale)
9. [Tradeoffs & Known Limitations](#tradeoffs--known-limitations)
10. [Running Tests](#running-tests)
11. [Environment Variables](#environment-variables)
12. [AI Tools Used](#ai-tools-used)

---

## Quick Start

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd ai_visibility_api

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY

# 4. Initialise the database
flask db init
flask db migrate -m "Initial migration"
flask db upgrade

# 5. Start the server
flask run
# → http://localhost:5000/api/v1
```

Or use the convenience script (Linux/macOS/WSL):

```bash
bash setup.sh
# Then: source .venv/bin/activate && flask run
```

---

## Docker Setup

```bash
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY (and optionally DATAFORSEO credentials)

docker-compose up --build
# → http://localhost:5000/api/v1
```

The Dockerfile runs `flask db upgrade` automatically before starting the server.

---

## API Reference

All endpoints return JSON. No authentication required.

### Register a business profile

```
POST /api/v1/profiles
```

```json
{
  "name": "Surfer SEO",
  "domain": "surferseo.com",
  "industry": "SEO Software",
  "description": "AI-powered SEO content optimization tool",
  "competitors": ["clearscope.io", "marketmuse.com", "frase.io"]
}
```

Returns `201` with `profile_uuid`.

### Get a profile (with stats)

```
GET /api/v1/profiles/{profile_uuid}
```

Returns profile fields plus `total_queries_discovered` and `avg_opportunity_score`.

### Trigger the pipeline

```
POST /api/v1/profiles/{profile_uuid}/run
```

Runs all three agents synchronously (10–30 s). Returns:
- `run_uuid`, `status` (`completed` / `failed`)
- `queries_discovered`, `queries_scored`, `tokens_used`
- `top_3_opportunity_queries`
- `content_recommendations`

### List queries

```
GET /api/v1/profiles/{profile_uuid}/queries
```

Query params:
| Param | Description |
|---|---|
| `min_score` | Float — filter by minimum opportunity score |
| `status` | `visible` \| `not_visible` \| `unknown` |
| `page` | Page number (default 1) |
| `per_page` | Results per page (default 20, max 100) |

### List recommendations

```
GET /api/v1/profiles/{profile_uuid}/recommendations
```

Returns recommendations sorted by priority (high → medium → low).

### Recheck a single query

```
POST /api/v1/queries/{query_uuid}/recheck
```

Re-runs Agent 2 on one query. Useful after publishing content.

### Error format

All errors follow a consistent structure:

```json
{ "error": "Human-readable message", "status": 404 }
```

---

## Architecture Decisions

### App factory pattern

`create_app(config_name)` in `app/__init__.py` initialises Flask, binds SQLAlchemy and
Flask-Migrate, registers blueprints, and attaches global error handlers. This allows:
- Multiple config environments (development / production / testing)
- Clean test isolation — each test suite can create its own app instance

### Blueprint structure

| Blueprint | Prefix | Responsibility |
|---|---|---|
| `profiles_bp` | `/api/v1` | Profile CRUD, pipeline trigger, query listing, recommendations listing |
| `queries_bp` | `/api/v1` | Query-level recheck endpoint |

Blueprints are registered inside `create_app()` (not at import time) to avoid circular imports.

### Synchronous pipeline

The pipeline runs synchronously as the spec does not require async. The three agents call the
Anthropic API sequentially; total wall time is typically 15–40 s depending on provider latency.
A future async implementation would use Celery + Redis and expose a `/runs/{uuid}/status`
polling endpoint.

### DataForSEO integration

The `DataForSEOClient` in `app/services/dataforseo.py` calls the
`/v3/keywords_data/google_ads/search_volume/live` endpoint for real monthly search volume and
competition index per keyword. If credentials are absent or the request fails, a heuristic
fallback (`_fallback_estimate`) is used so the pipeline degrades gracefully rather than crashing.

---

## Agent Design Rationale

### Agent 1 — QueryDiscoveryAgent (`app/agents/discovery.py`)

**Persona:** AI search analyst specialising in B2B SaaS competitive intelligence.

**Prompt strategy:**
- System prompt sets strict output format and quality criteria (commercial intent signals,
  competitor callouts, query length variety).
- User prompt injects the full business profile and specifies exact minimum counts per query
  category (≥ 4 comparisons, ≥ 3 best-of evaluators, etc.) to prevent generic output.
- The schema is defined inside the prompt as a concrete JSON example — not just described in
  prose — so the LLM has an unambiguous template to follow.

**Output validation:** Deduplicates by lowercased text; filters out queries shorter than 10
characters; coerces missing fields to safe defaults.

### Agent 2 — VisibilityScoringAgent (`app/agents/scoring.py`)

**Persona:** AI visibility analyst.

**Prompt strategy:**
- The agent simulates what a leading AI assistant would say, then evaluates domain presence.
- Crucially, the system prompt *explicitly instructs the model not to always include the target
  domain* — without this instruction the model tends to flatter by including it in every answer.
- Visibility position and context are only populated when `domain_visible` is `true` — the
  pipeline enforces this at the code level too.

**Data sources:**
- Search volume + difficulty: DataForSEO (real API) with heuristic fallback.
- Visibility simulation: Claude.

**Partial failure isolation:** The orchestrator wraps each Agent 2 call in try/except so one
malformed LLM response does not abort the remaining queries.

### Agent 3 — ContentRecommendationAgent (`app/agents/recommendation.py`)

**Persona:** AEO (Answer Engine Optimisation) content strategist.

**Prompt strategy:**
- Only the top-N queries where `domain_visible=False` are sent — Agent 3 focuses exclusively
  on gaps, not on queries where the domain already appears.
- The system prompt demands publication-ready specificity: titles must be usable as-is,
  rationales must name the specific gap (not give generic SEO advice), keywords must be
  long-tail and query-aligned.
- `max_tokens=6000` because recommendations need more output space than discovery/scoring.

---

## Opportunity Score Formula

```
opportunity_score = 0.35 × volume_score
                  + 0.25 × ease_score
                  + 0.30 × visibility_gap_score
                  + 0.10 × intent_score
```

### Components

| Component | Formula | Rationale |
|---|---|---|
| **volume_score** | `log10(max(vol, 1)) / log10(100_000)` → [0, 1] | Logarithmic: doubling budget-to-win effort doesn't double proportionally. 100 vol → 0.40, 1 000 → 0.60, 10 000 → 0.80, 100 000 → 1.0 |
| **ease_score** | `1.0 − (difficulty / 100)` | Linear inverse of competitive difficulty (0–100). A score of 1.0 means unchallenged territory. |
| **visibility_gap_score** | `1.0` if not visible · `0.5` if unknown · `0.1` if visible | Binary visibility check is the most direct signal — not appearing at all = maximum gap. Unknown queries (pre-scoring) get 0.5 neutral. Already visible = low urgency (0.1 not 0 so the query is still surfaced). |
| **intent_score** | `1.0` high · `0.6` medium · `0.2` low | High-commercial-intent queries ("vs", "best", "compare", "review") justify investment. Informational queries have lower ROI even with high volume. |

### Weight rationale

- **Volume (0.35) + Gap (0.30) = 65 %** of the score. This reflects the core definition of
  opportunity: a large volume where you are absent is the primary signal to act.
- **Ease (0.25)**: moderates by capture-ability — a huge gap in a 95-difficulty keyword may
  not be worth pursuing.
- **Intent (0.10)**: tie-breaker. Commercial queries justify content investment over
  informational ones with the same volume.

### Example calculations

| Volume | Difficulty | Visible | Query | Score |
|---|---|---|---|---|
| 10 000 | 10 | No | "best seo tool vs clearscope" | **~0.90** |
| 1 000 | 50 | No | "surfer seo review" | **~0.65** |
| 500 | 40 | Yes | "how to write seo content" | **~0.35** |
| 50 | 90 | Yes | "what is seo" | **~0.12** |

---

## Data Model & Schema Decisions

### `business_profiles`

- `uuid` (PK, string 36) — UUID4 generated in Python; portable across databases without
  auto-increment coupling.
- `competitors` (JSON) — stored as a JSON array rather than a junction table because
  competitors are a configuration input, not a first-class entity that needs its own queries.
- `status` — currently always `created`; reserved for future workflow states (e.g. `archived`).

### `pipeline_runs`

- Separate table (not embedded in profile) so every run is auditable independently.
- `tokens_used` enables cost tracking per run.
- `error_message` captures the first unrecoverable failure for debugging.

### `discovered_queries`

- `domain_visible` is **nullable boolean** (not `false` by default) to represent three states:
  `None` = not yet scored, `True` = visible, `False` = not visible. This maps cleanly to the
  `?status=unknown|visible|not_visible` filter and avoids misleading default values.
- `visibility_context` stores the exact sentence from the simulated AI response so reviewers
  can verify why a domain was marked visible.
- `commercial_intent_score` is persisted alongside `opportunity_score` so it can be queried and
  filtered independently in future analytics features.

### `content_recommendations`

- FK to both `profile_uuid` and `query_uuid` so recommendations can be fetched by either
  dimension (all recs for a profile, or all recs for a specific query gap).
- `target_keywords` (JSON array) — same rationale as competitors: a simple attribute, not a
  separate entity.

---

## Model Selection Rationale

All three agents use **Claude claude-sonnet-4-6** (Anthropic).

**Why Claude over GPT-4o?**

1. **Instruction adherence:** Claude consistently follows strict output-format instructions
   embedded in system prompts. This is critical because the pipeline must parse JSON from every
   agent response — a single malformed response causes a partial failure.

2. **JSON reliability:** In internal testing with similar prompts, Claude produces
   well-formed JSON more consistently than GPT-4o, especially for nested schemas.

3. **Contextual nuance for AEO:** Claude's training makes it a natural fit for simulating
   what AI assistants say in response to queries — the core of Agent 2's visibility check.

**Why one model for all three agents?**

Consistency. A single model means a single failure mode, single token pricing, and simpler
debugging. If cost becomes a concern, Agent 1 (discovery) and Agent 3 (recommendations) could
move to **Claude Haiku 4.5** for speed and cost without sacrificing the structured JSON quality
needed by Agent 2's nuanced visibility simulation.

---

## Tradeoffs & Known Limitations

| Tradeoff | Decision | Alternative |
|---|---|---|
| Synchronous pipeline | Simple, easy to debug; can timeout on slow networks | Celery + Redis + polling endpoint |
| Visibility simulation | Claude simulates AI responses — not querying real AI assistants | Call actual OpenAI/Perplexity APIs (cost, rate limits) |
| SQLite default | Zero-config for dev/test | PostgreSQL for production (set `DATABASE_URL`) |
| In-process JSON fallback | Three-attempt parse handles most LLM formatting variations | Stricter prompt-only enforcement (higher failure rate) |
| Opportunity score weights | Chosen by reasoned design; not empirically calibrated | A/B testing against actual traffic conversion data |
| No rate limiting | Keeps the implementation simple | Flask-Limiter on `/profiles/{uuid}/run` |

---

## Running Tests

```bash
# Activate your virtual environment first
pytest tests/ -v
```

Tests use mocked LLM responses (no real API calls). Coverage:
- `TestQueryDiscoveryAgent` — happy path, malformed JSON, markdown fencing, deduplication, filtering
- `TestVisibilityScoringAgent` — visible/not-visible, bool coercion, clamping, fallback
- `TestContentRecommendationAgent` — happy path, empty-query short-circuit, normalisation
- `TestOpportunityScoring` — formula components, range guarantees, intent detection

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | — | Anthropic API key (all three agents) |
| `DATAFORSEO_LOGIN` | No | — | DataForSEO email — enables real keyword data |
| `DATAFORSEO_PASSWORD` | No | — | DataForSEO API password |
| `DATABASE_URL` | No | `sqlite:///dev.db` | SQLAlchemy database URI |
| `FLASK_ENV` | No | `development` | `development` / `production` / `testing` |
| `SECRET_KEY` | No | hard-coded dev key | Flask secret key — **change in production** |
| `PORT` | No | `5000` | Port to listen on |

---

## AI Tools Used

**Claude Code (claude-sonnet-4-6)** — used to generate the initial project scaffold, agent
prompts, and unit test fixtures. All architectural decisions (schema design, scoring formula,
agent separation, failure handling strategy) were made and verified by the author. Prompts were
iteratively refined until the pipeline produced consistent, parseable output across diverse
business profiles.
