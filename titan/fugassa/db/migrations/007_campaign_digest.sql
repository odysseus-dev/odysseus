-- Campaign narrative digest (ADR §7 Tier 2) — upgrade path for existing saves
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaign_digest (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    digest_text TEXT NOT NULL DEFAULT '',
    mega_anchors_json TEXT NOT NULL DEFAULT '[]',
    last_condensed_turn INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO campaign_digest (id, digest_text) VALUES (1, '');

UPDATE campaign_settings SET save_version = 7 WHERE id = 1;
