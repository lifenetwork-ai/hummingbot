CREATE TABLE IF NOT EXISTS bot_liquidity_checkpoints (
    bot_id TEXT NOT NULL,
    token TEXT NOT NULL,
    payload JSONB NOT NULL,
    CONSTRAINT pk_bot_liquidity_checkpoints PRIMARY KEY (bot_id)
);
