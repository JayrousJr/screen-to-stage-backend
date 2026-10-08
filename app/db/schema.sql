CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    facility_id TEXT NOT NULL,
    patient_ref TEXT NOT NULL,
    image_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'complete', 'failed')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS results (
    scan_id TEXT PRIMARY KEY REFERENCES scans (id),
    findings TEXT NOT NULL,
    flagged_regions TEXT NOT NULL,
    body_part TEXT NOT NULL DEFAULT 'chest',
    conditions TEXT NOT NULL DEFAULT '[]',
    devices TEXT NOT NULL DEFAULT '[]',
    boxes TEXT NOT NULL DEFAULT '[]',
    comparison TEXT,
    confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    requires_review INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sync_queue (
    result_id TEXT PRIMARY KEY REFERENCES results (scan_id),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'synced')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_scans_status ON scans (status);
CREATE INDEX IF NOT EXISTS idx_sync_queue_status ON sync_queue (status);
