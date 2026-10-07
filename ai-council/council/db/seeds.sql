-- =============================================================================
-- Seed default agents. Models live in this table; the optional *_MODEL_* env vars
-- override them at runtime (see council/config.py).
-- =============================================================================
INSERT INTO agents (id, display_name, emoji, role_name, role_type, provider,
                    model_normal, model_escalated, escalation_policy,
                    max_tokens, auto_call_approved, responsibilities, restrictions)
VALUES
    ('codex', 'CODEX', '🤖', 'Project Manager / Moderator', 'MODERATOR', 'openai',
     'gpt-5.6-terra', 'gpt-5.6-sol', 'AUTO_ON_CRITICAL', 4000, FALSE,
     ARRAY['Synthesize discussion', 'Moderate debate', 'Extract disagreements', 'Build consensus'],
     ARRAY['Cannot self-approve decisions', 'Cannot create new agents', 'Cannot increase budget']),

    ('claude', 'CLAUDE', '🔬', 'Architecture Critic', 'CRITIC', 'anthropic',
     'claude-sonnet-4-6', 'claude-opus-5-5', 'CEO_ONLY', 4000, FALSE,
     ARRAY['Critical architecture review', 'Security analysis', 'Identify design flaws'],
     ARRAY['Cannot approve decisions', 'Cannot deploy']),

    ('gemini', 'GEMINI', '🔭', 'Research & Alternatives', 'RESEARCHER', 'google',
     'gemini-3.8-flash', 'gemini-3.1-pro-preview', 'CEO_ONLY', 4000, FALSE,
     ARRAY['Research alternatives', 'Benchmark comparisons', 'Market analysis'],
     ARRAY['Cannot approve decisions', 'Cannot deploy'])
ON CONFLICT (id) DO NOTHING;
