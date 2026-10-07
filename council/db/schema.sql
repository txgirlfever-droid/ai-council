-- =============================================================================
-- AI COUNCIL MVP — PostgreSQL Schema
-- Architecture v0.2.1 FINAL
-- Priority: Correctness → Idempotency → Auditability → Failure recovery
-- =============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- =============================================================================
-- USERS
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id   BIGINT UNIQUE NOT NULL,  -- auth by this ONLY
    username      TEXT,                    -- display only, not used for auth
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'CEO',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- PROJECTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS projects (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    description   TEXT,
    state         TEXT NOT NULL DEFAULT 'PLANNING'
        CHECK (state IN ('PLANNING', 'DEVELOPMENT', 'PRODUCTION', 'ARCHIVED')),
    access_mode   TEXT NOT NULL DEFAULT 'READ_DISCUSS'
        CHECK (access_mode IN ('READ_DISCUSS', 'READ_WRITE')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- PROJECT CONTEXT ENTRIES (versioned, effectively immutable per version)
-- Correction #11: Partial UNIQUE index ensures only 1 current version per key
-- =============================================================================
CREATE TABLE IF NOT EXISTS project_context_entries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES projects(id),
    entry_type       TEXT NOT NULL
        CHECK (entry_type IN ('REQUIREMENT', 'ARCHITECTURE', 'CONSTRAINT', 'ASSUMPTION', 'APPROVED_DECISION')),
    entry_key        TEXT NOT NULL,
    version          INT NOT NULL DEFAULT 1 CHECK (version > 0),   -- Correction: CHECK version > 0
    display_version  TEXT,
    content          TEXT NOT NULL,
    source           TEXT,
    supersedes_id    UUID REFERENCES project_context_entries(id),
    is_current       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- NO updated_at: entries are immutable once created
);

-- Correction #11: Only 1 current version per (project, entry_key)
CREATE UNIQUE INDEX IF NOT EXISTS idx_context_one_current
    ON project_context_entries(project_id, entry_key)
    WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_context_by_type
    ON project_context_entries(project_id, entry_type)
    WHERE is_current = TRUE;

-- =============================================================================
-- CONTEXT SNAPSHOTS (immutable)
-- =============================================================================
CREATE TABLE IF NOT EXISTS context_snapshots (
    id                  TEXT PRIMARY KEY,  -- 'CTX-2026-0012'
    project_id          UUID NOT NULL REFERENCES projects(id),
    snapshot_data       JSONB NOT NULL,    -- denormalized copy; immutable
    entry_ids           UUID[] NOT NULL,
    arch_version        TEXT,
    req_version         TEXT,
    approved_decisions  TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- NO updated_at — strictly immutable
);

-- =============================================================================
-- AGENT PROMPT VERSIONS (immutable) — Correction #9
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_prompt_versions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id     TEXT NOT NULL,
    version      INT NOT NULL CHECK (version > 0),
    prompt_text  TEXT NOT NULL,
    prompt_hash  TEXT NOT NULL,   -- SHA-256 of prompt_text
    is_current   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by   UUID REFERENCES users(id)
    -- NO updated_at — immutable
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_one_current
    ON agent_prompt_versions(agent_id)
    WHERE is_current = TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_version_unique
    ON agent_prompt_versions(agent_id, version);

-- =============================================================================
-- AGENTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS agents (
    id                        TEXT PRIMARY KEY,
    display_name              TEXT NOT NULL,
    emoji                     TEXT NOT NULL,
    role_name                 TEXT NOT NULL,
    role_type                 TEXT NOT NULL
        CHECK (role_type IN ('MODERATOR', 'CRITIC', 'RESEARCHER', 'SPECIALIST')),
    provider                  TEXT NOT NULL
        CHECK (provider IN ('openai', 'anthropic', 'google')),
    model_normal              TEXT NOT NULL,
    model_escalated           TEXT,
    escalation_policy         TEXT
        CHECK (escalation_policy IN ('CEO_ONLY', 'AUTO_ON_CRITICAL') OR escalation_policy IS NULL),
    current_prompt_version_id UUID REFERENCES agent_prompt_versions(id),
    max_tokens                INT NOT NULL DEFAULT 4000,
    is_specialist             BOOLEAN NOT NULL DEFAULT FALSE,
    auto_call_approved        BOOLEAN NOT NULL DEFAULT FALSE,  -- Correction N3 whitelist
    responsibilities          TEXT[],
    restrictions              TEXT[],
    enabled                   BOOLEAN NOT NULL DEFAULT TRUE,
    config_json               JSONB,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- API keys: NOT HERE — environment variables only
);

-- =============================================================================
-- DISCUSSIONS
-- =============================================================================
CREATE SEQUENCE IF NOT EXISTS discussion_seq START 1;

CREATE TABLE IF NOT EXISTS discussions (
    id                    TEXT PRIMARY KEY,  -- 'DISC-021'
    seq_number            INT DEFAULT nextval('discussion_seq'),
    project_id            UUID NOT NULL REFERENCES projects(id),
    title                 TEXT NOT NULL,
    question              TEXT NOT NULL,
    criticality           TEXT NOT NULL DEFAULT 'NORMAL'
        CHECK (criticality IN ('NORMAL', 'CRITICAL')),
    -- State machine
    base_state            TEXT NOT NULL DEFAULT 'CREATED'
        CHECK (base_state IN (
            'CREATED', 'INDEPENDENT_REVIEW', 'CROSS_REVIEW', 'DEBATE',
            'RED_TEAM', 'BLUE_TEAM', 'SYNTHESIS', 'CEO_REVIEW',
            'APPROVED', 'REJECTED', 'DEFERRED', 'PAUSED', 'CANCELLED'
        )),
    interaction_state     TEXT
        CHECK (interaction_state IN ('CEO_INTERRUPT', 'AWAITING_ESCALATION_APPROVAL') OR interaction_state IS NULL),
    previous_base_state   TEXT,
    context_snapshot_id   TEXT REFERENCES context_snapshots(id),
    telegram_topic_id     BIGINT,
    telegram_group_id     BIGINT,
    round_current         INT NOT NULL DEFAULT 0,
    active_ceo_review_id  UUID,       -- Correction #2
    cost_soft_limit_usd   DECIMAL(10,4) NOT NULL DEFAULT 2.00,
    cost_hard_limit_usd   DECIMAL(10,4) NOT NULL DEFAULT 5.00,
    cost_total_usd        DECIMAL(10,4) NOT NULL DEFAULT 0.00,
    cost_reserved_usd     DECIMAL(10,4) NOT NULL DEFAULT 0.00,
    soft_limit_notified   BOOLEAN NOT NULL DEFAULT FALSE,
    agents_invited        TEXT[],
    created_by            UUID REFERENCES users(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at             TIMESTAMPTZ
);

-- =============================================================================
-- CEO REVIEWS — Correction #2: Business Idempotency Layer
-- =============================================================================
CREATE TABLE IF NOT EXISTS ceo_reviews (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_action_key  TEXT UNIQUE NOT NULL,  -- semantic idempotency key
    discussion_id        TEXT NOT NULL REFERENCES discussions(id),
    synthesis_run_id     TEXT,
    status               TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'DECIDED', 'EXPIRED')),
    options_presented    JSONB NOT NULL,
    telegram_msg_id      BIGINT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at           TIMESTAMPTZ
);

-- =============================================================================
-- ROUNDS
-- =============================================================================
CREATE TABLE IF NOT EXISTS rounds (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discussion_id              TEXT NOT NULL REFERENCES discussions(id),
    round_number               INT NOT NULL,
    round_type                 TEXT NOT NULL
        CHECK (round_type IN (
            'INDEPENDENT_REVIEW', 'CROSS_REVIEW', 'DEBATE',
            'RED_TEAM', 'BLUE_TEAM', 'SYNTHESIS'
        )),
    state                      TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (state IN ('PENDING', 'ACTIVE', 'PARTIAL_COMPLETE', 'COMPLETE', 'PAUSED', 'FAILED')),
    agents_requested           TEXT[] NOT NULL,
    completion_policy          TEXT NOT NULL DEFAULT 'QUORUM'
        CHECK (completion_policy IN ('ALL_RESPONDED', 'QUORUM')),
    quorum_threshold           INT NOT NULL DEFAULT 2,
    requires_non_codex         BOOLEAN NOT NULL DEFAULT TRUE,  -- Correction #4
    -- Correction #12: Timeout taxonomy (3 types, separate)
    agent_execution_timeout_s  INT NOT NULL DEFAULT 180,
    round_timeout_s            INT NOT NULL DEFAULT 600,
    -- No discussion_timeout — discussions live until CEO decides
    disagreement_ids           TEXT[],
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at                 TIMESTAMPTZ,
    completed_at               TIMESTAMPTZ,
    UNIQUE (discussion_id, round_number)
);

-- Essential index — Correction MUST HAVE
CREATE INDEX IF NOT EXISTS idx_rounds_discussion_id ON rounds(discussion_id);

-- =============================================================================
-- AGENT RUNS
-- =============================================================================
CREATE SEQUENCE IF NOT EXISTS agent_run_seq START 1;

CREATE TABLE IF NOT EXISTS agent_runs (
    id                   TEXT PRIMARY KEY,  -- 'MSG-000234'
    seq_number           INT DEFAULT nextval('agent_run_seq'),
    internal_id          UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    round_id             UUID NOT NULL REFERENCES rounds(id),
    discussion_id        TEXT NOT NULL REFERENCES discussions(id),
    agent_id             TEXT NOT NULL REFERENCES agents(id),
    provider             TEXT NOT NULL,
    model_used           TEXT NOT NULL,
    is_escalated         BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_version_id    UUID REFERENCES agent_prompt_versions(id),  -- Correction #9
    council_packet_id    TEXT,
    packet_hash          TEXT,
    context_snapshot_id  TEXT REFERENCES context_snapshots(id),
    parent_run_id        TEXT REFERENCES agent_runs(id),
    status               TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'TIMEOUT', 'SKIPPED')),
    error_message        TEXT,
    retry_count          INT NOT NULL DEFAULT 0,
    max_retries          INT NOT NULL DEFAULT 3,
    -- Correction #10: Structured response
    response_structured  JSONB,
    response_raw         TEXT,
    response_valid       BOOLEAN,
    telegram_msg_id      BIGINT,
    telegram_reply_to    BIGINT,
    tokens_input         INT,
    tokens_output        INT,
    cost_usd             DECIMAL(10,6),
    audit_payload        JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ
);

-- Essential indexes — MUST HAVE (real query paths)
CREATE INDEX IF NOT EXISTS idx_agent_runs_discussion_id ON agent_runs(discussion_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_round_id ON agent_runs(round_id);

-- =============================================================================
-- COUNCIL PACKETS — Correction #5: retention-aware
-- =============================================================================
CREATE TABLE IF NOT EXISTS council_packets (
    id               TEXT PRIMARY KEY,
    packet_hash      TEXT NOT NULL UNIQUE,
    discussion_id    TEXT NOT NULL REFERENCES discussions(id),
    round_type       TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    round_number     INT NOT NULL,
    retention_policy TEXT NOT NULL DEFAULT 'STANDARD_90D'
        CHECK (retention_policy IN ('STANDARD_90D', 'DECISION_AUDIT')),
    protected_by_decision_id TEXT,   -- FK added after decisions table
    packet_json      JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ    -- NULL = no auto-purge (DECISION_AUDIT)
);

-- =============================================================================
-- STRUCTURED DISAGREEMENTS
-- =============================================================================
CREATE SEQUENCE IF NOT EXISTS disagreement_seq START 1;

CREATE TABLE IF NOT EXISTS disagreements (
    id              TEXT PRIMARY KEY,  -- 'DIS-001'
    seq_number      INT DEFAULT nextval('disagreement_seq'),
    discussion_id   TEXT NOT NULL REFERENCES discussions(id),
    round_id        UUID REFERENCES rounds(id),
    claim           TEXT NOT NULL,
    description     TEXT,
    position_a_agent    TEXT REFERENCES agents(id),
    position_a_label    TEXT,
    position_a_run_id   TEXT REFERENCES agent_runs(id),
    position_b_agent    TEXT REFERENCES agents(id),
    position_b_label    TEXT,
    position_b_run_id   TEXT REFERENCES agent_runs(id),
    -- Correction #8: Debate cycle tracking
    cycles_completed    INT NOT NULL DEFAULT 0,
    max_cycles          INT NOT NULL DEFAULT 3,  -- N5 locked = 3
    status          TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'DEBATING', 'RESOLVED', 'PARTIALLY_RESOLVED',
                          'UNRESOLVED', 'ESCALATED_TO_CEO', 'DEFERRED')),
    resolution_note TEXT,
    resolved_by     TEXT,
    related_run_ids TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

-- =============================================================================
-- DECISIONS (append-only, immutable) — Correction #1
-- =============================================================================
CREATE SEQUENCE IF NOT EXISTS decision_seq START 1;

CREATE TABLE IF NOT EXISTS decisions (
    id               TEXT PRIMARY KEY,  -- 'DEC-032'
    seq_number       INT DEFAULT nextval('decision_seq'),
    version          INT NOT NULL DEFAULT 1 CHECK (version > 0),
    discussion_id    TEXT NOT NULL REFERENCES discussions(id),
    project_id       UUID NOT NULL REFERENCES projects(id),
    context_snapshot_id TEXT REFERENCES context_snapshots(id),
    ceo_review_id    UUID NOT NULL REFERENCES ceo_reviews(id),  -- Correction #2
    title            TEXT NOT NULL,
    status           TEXT NOT NULL
        CHECK (status IN ('APPROVED', 'REJECTED', 'DEFERRED')),
    option_selected  TEXT,
    options_json     JSONB NOT NULL,
    agent_recs_json  JSONB NOT NULL,
    dissent_json     JSONB,           -- NEVER deleted
    ceo_reason       TEXT,
    -- Correction #1: Only supersedes_id on NEW record; NEVER update old record
    supersedes_id    TEXT REFERENCES decisions(id),
    telegram_msg_id  BIGINT,
    decided_by       UUID NOT NULL REFERENCES users(id),
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- NO updated_at — strictly immutable after INSERT
);

-- Essential index — MUST HAVE
CREATE INDEX IF NOT EXISTS idx_decisions_ceo_review_id ON decisions(ceo_review_id);
CREATE INDEX IF NOT EXISTS idx_decisions_supersedes ON decisions(supersedes_id)
    WHERE supersedes_id IS NOT NULL;

-- Late FK: council_packets → decisions
ALTER TABLE council_packets
    ADD CONSTRAINT IF NOT EXISTS fk_packet_decision
    FOREIGN KEY (protected_by_decision_id) REFERENCES decisions(id);

-- =============================================================================
-- RISKS
-- =============================================================================
CREATE SEQUENCE IF NOT EXISTS risk_seq START 1;

CREATE TABLE IF NOT EXISTS risks (
    id              TEXT PRIMARY KEY,  -- 'RISK-018'
    seq_number      INT DEFAULT nextval('risk_seq'),
    discussion_id   TEXT REFERENCES discussions(id),
    project_id      UUID NOT NULL REFERENCES projects(id),
    title           TEXT NOT NULL,
    description     TEXT,
    likelihood      TEXT NOT NULL CHECK (likelihood IN ('LOW', 'MEDIUM', 'HIGH')),
    impact          TEXT NOT NULL CHECK (impact IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    severity        TEXT NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    detected_by     TEXT REFERENCES agents(id),
    source_run_id   TEXT REFERENCES agent_runs(id),
    status          TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'MITIGATED', 'ACCEPTED', 'CLOSED')),
    mitigation_note TEXT,
    telegram_msg_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

-- =============================================================================
-- IDEAS
-- =============================================================================
CREATE TABLE IF NOT EXISTS ideas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discussion_id   TEXT REFERENCES discussions(id),
    project_id      UUID NOT NULL REFERENCES projects(id),
    content         TEXT NOT NULL,
    source_agent    TEXT REFERENCES agents(id),
    source_run_id   TEXT REFERENCES agent_runs(id),
    tags            TEXT[],
    status          TEXT NOT NULL DEFAULT 'LOGGED'
        CHECK (status IN ('LOGGED', 'PROMOTED', 'DISMISSED')),
    telegram_msg_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- USAGE COSTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS usage_costs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_run_id    TEXT NOT NULL REFERENCES agent_runs(id),
    discussion_id   TEXT NOT NULL REFERENCES discussions(id),
    agent_id        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model_used      TEXT NOT NULL,
    tokens_input    INT NOT NULL DEFAULT 0,
    tokens_output   INT NOT NULL DEFAULT 0,
    cost_usd        DECIMAL(10,6) NOT NULL DEFAULT 0,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- AUDIT LOG (immutable, append-only)
-- =============================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor           TEXT NOT NULL,
    actor_user_id   UUID REFERENCES users(id),
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    detail_json     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- NO updated_at, NO delete — append-only
);

-- =============================================================================
-- TELEGRAM EVENTS — Layer 1 Idempotency
-- =============================================================================
CREATE TABLE IF NOT EXISTS telegram_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_update_id  BIGINT UNIQUE NOT NULL,   -- Layer 1 dedup key
    callback_query_id   TEXT,
    idempotency_key     TEXT UNIQUE,
    event_type          TEXT NOT NULL
        CHECK (event_type IN ('COMMAND', 'MESSAGE', 'CALLBACK')),
    from_user_id        BIGINT NOT NULL,
    raw_payload         JSONB NOT NULL,
    payload_hash        TEXT NOT NULL,
    parsed_intent       TEXT,
    processing_status   TEXT NOT NULL DEFAULT 'RECEIVED'
        CHECK (processing_status IN ('RECEIVED', 'PROCESSING', 'DONE', 'FAILED', 'DUPLICATE')),
    related_entity_type TEXT,
    related_entity_id   TEXT,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at        TIMESTAMPTZ
);

-- =============================================================================
-- JOB QUEUE — Correction #3: SELECT FOR UPDATE SKIP LOCKED + lease fields
-- =============================================================================
CREATE TABLE IF NOT EXISTS job_queue (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type         TEXT NOT NULL
        CHECK (job_type IN (
            'RUN_AGENT', 'POST_TELEGRAM', 'COMPLETE_ROUND',
            'SYNTHESIZE', 'NOTIFY_CEO', 'CHECK_TIMEOUT',
            'CHECK_LEASE', 'RECOVER_STALE'
        )),
    payload          JSONB NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED', 'STALE')),
    idempotency_key  TEXT UNIQUE,
    priority         INT NOT NULL DEFAULT 5,   -- 1=highest
    attempt_count    INT NOT NULL DEFAULT 0,
    max_attempts     INT NOT NULL DEFAULT 3,
    error_message    TEXT,
    -- Correction #3: Worker lease fields
    locked_by        TEXT,
    locked_at        TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at     TIMESTAMPTZ,
    scheduled_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- MUST HAVE indexes (real query paths)
CREATE INDEX IF NOT EXISTS idx_job_claimable
    ON job_queue(priority, scheduled_at)
    WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_job_stale
    ON job_queue(lease_expires_at)
    WHERE status = 'RUNNING';

-- Immutable/audit tables are append-only at the database boundary.
CREATE OR REPLACE FUNCTION reject_mutation_of_immutable_row()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS trg_decisions_append_only ON decisions;
CREATE TRIGGER trg_decisions_append_only
BEFORE UPDATE OR DELETE ON decisions
FOR EACH ROW EXECUTE FUNCTION reject_mutation_of_immutable_row();

DROP TRIGGER IF EXISTS trg_audit_log_append_only ON audit_log;
CREATE TRIGGER trg_audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION reject_mutation_of_immutable_row();
