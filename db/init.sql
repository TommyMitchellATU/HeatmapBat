-- Database bootstrap for local development
-- Enable PostGIS extension providing spatial types and functions
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS maug_summary_samples (
    id SERIAL PRIMARY KEY,
    timestamp_utc TIMESTAMP NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    power_v DOUBLE PRECISION,
    temp_c DOUBLE PRECISION,
    files_count INTEGER,
    scrubbed_count INTEGER,
    mic0_type TEXT,
    raw_date TEXT,
    raw_time TEXT
);