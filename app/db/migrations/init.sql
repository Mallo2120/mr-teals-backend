-- SQL schema for the Mr. Teals trading bot.
-- Run this script against your Postgres database to create the
-- necessary tables. These tables are intentionally kept simple and
-- focused on the information you need to store for a basic bot. Feel
-- free to extend them with additional fields as your strategies
-- evolve (e.g. stop prices, fees, order IDs, etc.).

-- Trades table records each executed trade.
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(4) NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    price NUMERIC(20, 8) NOT NULL,
    time TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    strategy VARCHAR(20)
);

-- Positions table tracks current open positions. When positions close
-- you may choose to archive them in another table or update the status.
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    avg_price NUMERIC(20, 8) NOT NULL,
    open_time TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    status VARCHAR(10) NOT NULL DEFAULT 'open'
);

-- Performance table stores aggregated daily performance metrics.
CREATE TABLE IF NOT EXISTS performance (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    realized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
    trades_count INTEGER NOT NULL DEFAULT 0
);

-- Settings table stores key/value pairs for configurable settings.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Watchlist table stores the list of symbols being monitored/traded.
CREATE TABLE IF NOT EXISTS watchlist (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) UNIQUE NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
