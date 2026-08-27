CREATE TABLE claimable_queue_settings (
    guild_id BIGINT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE ticket_claims (
    channel_id BIGINT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    claimed_by BIGINT,
    claimed_at TIMESTAMPTZ
)