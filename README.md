# AI Council MVP

> Telegram-based AI advisory council where CEO orchestrates discussions between multiple AI agents (Codex/Claude/Gemini), with PostgreSQL as source of truth.

## Architecture

See `docs/ARCHITECTURE_v0.2.1.md` for the full spec.

🇻🇳 Hướng dẫn dễ hiểu cho người không rành kỹ thuật, kèm sơ đồ:
[`docs/HUONG_DAN.md`](docs/HUONG_DAN.md)

## Quick Start

```bash
# 1. Install (Python 3.11+, PostgreSQL 14+)
git clone <repo>
cd ai-council
pip install -e ".[dev]"

# 2. Configure: copy the example and fill in your tokens and keys
cp .env.example .env

# 3. Create the database tables
python -m council.db.migrate

# 4. Tell Telegram where your server is (needs WEBHOOK_URL and WEBHOOK_SECRET)
python -m council.webhook.register

# 5. Start the worker and the webhook (two terminals)
python -m council.worker
python -m council.webhook
```

An agent only joins the council when its provider key (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) is set; at least two agents, one of them
not Codex, are needed for a quorum.

## Using the bot (CEO only)

| Command | What happens |
|---|---|
| `/council <question>` | The council reviews, cross-reviews, debates (up to 3 cycles) and posts a synthesis with **Approve / Reject / Defer** buttons. |
| `/council_critical <question>` | Same, but every agent must answer and Codex uses its escalated model. |
| `/status` | Open discussions, queue size, timeouts and cost limits. |
| `/help` | Command list. |

Pressing a decision button records an immutable decision (`DEC-…`) exactly once;
a second press is answered with the decision that already stands. If too few
agents answer, the discussion is **paused** and the CEO is told why — no result
is invented.

## Project Structure

```
council/
├── db/
│   ├── schema.sql          # Full PostgreSQL schema (Phase 1)
│   ├── migrate.py          # Migration runner
│   └── seeds.sql           # Default agent configs
├── webhook/
│   ├── handler.py          # Telegram webhook (async ACK)
│   ├── register.py         # Registers the webhook URL + secret with Telegram
│   └── service.py          # Deterministic idempotency keys/intake ledger
├── orchestrator/
│   ├── engine.py           # Discussion/round/debate/synthesis state machine
│   ├── prompts.py          # Role + round prompts with the JSON response contract
│   ├── quorum.py           # Quorum logic (N1)
│   └── service.py          # Worker job handlers: council runs + CEO decision gate
├── agents/
│   ├── base.py             # ProviderAdapter ABC
│   ├── openai_adapter.py   # OpenAI adapter
│   ├── anthropic_adapter.py# Anthropic adapter
│   ├── google_adapter.py   # Google adapter
│   └── runner.py           # Retry and provider-timeout boundary
├── worker/
│   ├── __main__.py         # Worker entry point
│   ├── worker.py           # Job queue consumer (SELECT FOR UPDATE SKIP LOCKED)
│   └── lease.py            # Lease transition rules
├── telegram/
│   ├── sender.py           # Telegram client boundary (swappable in tests)
│   ├── format.py           # CEO-facing message text
│   └── buttons.py          # Approve/Reject/Defer keyboard (64-byte callback data)
├── schemas/
│   ├── council_response.py # Structured JSON response schema (Correction #10)
├── costs/
│   └── tracker.py          # Cost tracking + soft/hard limits
└── audit/
    └── log.py              # Append-only audit log
tests/
├── test_idempotency.py     # Idempotency tests (MUST HAVE)
├── test_quorum.py          # Quorum logic tests
├── test_flow.py            # Full integration flow (mock providers)
├── test_mvp_integration.py # Webhook→rounds→CEO + timeout/race/lease
├── test_postgres_e2e.py    # Same flow on a real PostgreSQL (opt-in)
└── conftest.py             # Test fixtures
```

## Priority Order

**Correctness → Idempotency → Auditability → Failure recovery → Tests → Performance → Scale**

## MVP Scope

Redis, Kubernetes, Prometheus, partitioning, and distributed caching are explicitly deferred.

## Verification

```bash
python -m pytest -q
python -m ruff check .

# Full flow on a real, disposable PostgreSQL database (its public schema is reset):
TEST_DATABASE_URL=postgresql://council:secret@localhost:5432/ai_council_test python -m pytest -q
```

Provider calls are mocked by default. External API tests require explicit credentials and opt-in.
