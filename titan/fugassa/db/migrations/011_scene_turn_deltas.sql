-- Per-turn scene deltas (Sprint 2 G4) + exit rollup on scene_summaries

ALTER TABLE scene_summaries ADD COLUMN delta_text TEXT;

CREATE TABLE IF NOT EXISTS scene_turn_deltas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    turn_number INTEGER NOT NULL,
    delta_text TEXT NOT NULL,
    player_excerpt TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(location_id, turn_number),
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scene_turn_deltas_location_turn
    ON scene_turn_deltas(location_id, turn_number DESC);
