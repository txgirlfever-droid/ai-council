# AI Project Council Architecture v0.2.1 FINAL

This document records the locked MVP implemented by this repository.

## Principles

- CEO is the final authority. Agents detect, analyze, recommend, and wait.
- Roles are independent from provider model names. Model/provider changes are configuration changes and are never automatic.
- PostgreSQL is the sole state and queue authority. No in-memory fallback queue exists.
- Decisions, context snapshots, prompt versions, and audit events are immutable history.
- Processing is idempotent/effectively-once at the business-effect layer; it is not described as absolute exactly-once delivery.
- Default responses are concise. Detailed analysis is generated only when requested or operationally necessary.

## Flow

`Telegram webhook → durable event → job queue → discussion → immutable context snapshot → independent review → cross-review → up to three debate cycles → synthesis → CEO review → append-only decision`

Normal quorum is `max(2, ceil(2N/3))` and must include at least one non-Codex agent. Critical discussions require all requested agents. Partial results are labeled as partial and never called unanimous.

## Idempotency and concurrency

- `telegram_events.telegram_update_id` rejects Telegram retries.
- `job_queue.idempotency_key` rejects duplicate work.
- `ceo_reviews.decision_action_key` plus a transactional decision gate ensures competing approvals have one winner.
- Workers claim jobs using `FOR UPDATE SKIP LOCKED`, renew a 30-second heartbeat, and recover expired leases.

## Cost and timeout controls

Cost is checked and reserved before each provider call. New calls stop at the hard limit; in-flight overshoot remains visible in the audit trail. Agent and round timeouts are distinct and exposed by `/status`.

## Security

Only numeric Telegram user ID authorizes CEO operations. Telegram webhook secret tokens are checked using the official header. Provider keys and Telegram tokens exist only in environment variables and `.env` is ignored.

## MVP phases

1. PostgreSQL schema, constraints, indexes, Alembic migration, seed configuration.
2. Telegram authentication, webhook ingestion, durable jobs, audit log.
3. Provider adapters, structured response validation, cost reservation, timeouts.
4. Council state machine, quorum, disagreement/debate, synthesis, CEO decision gate.
5. Heartbeat/lease recovery, health/status endpoints, unit and integration verification.

## Explicitly deferred

Redis, Kubernetes, Prometheus/Grafana, partitioning, materialized views, distributed cache, automatic model upgrades, and any second queue source are outside this MVP.
