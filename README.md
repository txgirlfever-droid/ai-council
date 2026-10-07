# AI Council MVP

> Telegram-based AI advisory council where CEO orchestrates discussions between multiple AI agents (Codex/Claude/Gemini), with PostgreSQL as source of truth.

## Architecture

See `docs/ARCHITECTURE_v0.2.1.md` for the full spec.

## Quick Start

```bash
# 1. Clone and install
git clone <repo>
cd ai-council
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env with your tokens

# 3. Run migrations
python -m council.db.migrate

# 4. Start worker + webhook
python -m council.worker
# in another terminal
python -m council.webhook
```

## Project Structure

```
council/
├── db/
│   ├── schema.sql          # Full PostgreSQL schema (Phase 1)
│   ├── migrate.py          # Migration runner
│   └── seeds.sql           # Default agent configs
├── webhook/
│   ├── handler.py          # Telegram webhook (async ACK)
│   └── service.py          # Deterministic idempotency keys/intake ledger
├── orchestrator/
│   ├── engine.py           # Discussion/round/debate/synthesis state machine
│   ├── quorum.py           # Quorum logic (N1)
│   └── ...                 # Pure orchestration boundaries
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
│   ├── sender.py           # Idempotent message posting
│   └── buttons.py          # Inline keyboard builder
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
```

Provider calls are mocked by default. External API tests require explicit credentials and opt-in.
