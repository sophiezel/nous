-- Schema for Neon PostgreSQL (migrated from SQLite)
-- Run once to set up the database

CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reports_type_date ON reports(type, created_at DESC);

CREATE TABLE IF NOT EXISTS macro_scores (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    score REAL,
    position REAL,
    indicators JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sentiment (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    score INTEGER,
    limit_up_count INTEGER,
    limit_up_rate REAL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
