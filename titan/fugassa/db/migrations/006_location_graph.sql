-- Sublocation graph — upgrade path for existing saves (ADR §A "sublokace jako graf")
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS location_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_location_id INTEGER NOT NULL,
    to_location_id INTEGER NOT NULL,
    connection_type TEXT NOT NULL DEFAULT 'leads_to' CHECK (connection_type IN ('leads_to', 'contains', 'adjacent')),
    label TEXT,
    is_locked INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0, 1)),
    lock_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_location_id, to_location_id, connection_type),
    FOREIGN KEY (from_location_id) REFERENCES locations(id) ON DELETE CASCADE,
    FOREIGN KEY (to_location_id) REFERENCES locations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_location_connections_from ON location_connections(from_location_id);

UPDATE campaign_settings SET save_version = 6 WHERE id = 1;
