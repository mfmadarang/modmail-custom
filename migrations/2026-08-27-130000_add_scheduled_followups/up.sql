CREATE TABLE followup_settings (
    guild_id BIGINT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    followup_message TEXT
);

CREATE TABLE follow_ups (
    id SERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggered BOOLEAN NOT NULL DEFAULT FALSE
);