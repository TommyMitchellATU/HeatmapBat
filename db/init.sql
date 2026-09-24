-- Database bootstrap for local development
-- Enable PostGIS extension providing spatial types and functions
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS maug_summary_samples (
    id SERIAL PRIMARY KEY,
    site_id TEXT,
    detector_serial TEXT,
    source_folder TEXT,
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

-- Ensure the column exists even if the table was created before site_id was added.
ALTER TABLE IF EXISTS maug_summary_samples
    ADD COLUMN IF NOT EXISTS site_id TEXT;

-- Detector serial and source-folder flag; safe to re-run against an existing database.
ALTER TABLE IF EXISTS maug_summary_samples
    ADD COLUMN IF NOT EXISTS detector_serial TEXT,
    ADD COLUMN IF NOT EXISTS source_folder TEXT;
