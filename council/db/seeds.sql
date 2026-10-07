-- =============================================================================
-- Seed default agents (models configurable via env vars)
-- =============================================================================
INSERT INTO agents (id, display_name, emoji, role_name, role_type, provider,
                    model_normal, model_escalated, escalation_policy,
                    max_tokens, auto_call_approved, responsibilities, restrictions)
VALUES
    ('codex', 'CODEX', '🤖', 'Project Manager / Moderator', 'MODERATOR', 'openai',
     'gpt-4o', 'o1', 'AUTO_ON_CRITICAL', 4000, FALSE,
     ARRAY['Synthesize discussion', 'Moderate debate', 'Extract disagreements', 'Build consensus'],
     ARRAY['Cannot self-approve decisions', 'Cannot create new agents', 'Cannot increase budget']),

    ('claude', 'CLAUDE', '🔬', 'Architecture Critic', 'CRITIC', 'anthropic',
     'claude-sonnet-4-6', 'claude-opus-4-5', 'CEO_ONLY', 4000, FALSE,
     ARRAY['Critical architecture review', 'Security analysis', 'Identify design flaws'],
     ARRAY['Cannot approve decisions', 'Cannot deploy']),

    ('gemini', 'GEMINI', '🔭', 'Research & Alternatives', 'RESEARCHER', 'google',
     'gemini-1.5-pro', 'gemini-1.5-ultra', 'CEO_ONLY', 4000, FALSE,
     ARRAY['Research alternatives', 'Benchmark comparisons', 'Market analysis'],
     ARRAY['Cannot approve decisions', 'Cannot deploy'])
ON CONFLICT (id) DO NOTHING;
