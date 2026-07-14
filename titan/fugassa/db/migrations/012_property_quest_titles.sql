-- Property holdings (wizard seed + archivist), quest metadata, title bonuses.

CREATE TABLE IF NOT EXISTS property_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    player_character_id INTEGER NOT NULL,
    root_location_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    property_kind TEXT NOT NULL DEFAULT 'townhouse',
    title_status TEXT NOT NULL DEFAULT 'owned',
    acquired_at_turn INTEGER,
    acquired_via TEXT,
    deed_summary TEXT,
    specs_json TEXT NOT NULL DEFAULT '{}',
    valuation_gp REAL,
    upkeep_gp_per_month REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (player_character_id) REFERENCES player_characters(id) ON DELETE CASCADE,
    FOREIGN KEY (root_location_id) REFERENCES locations(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_property_holdings_pc ON property_holdings(player_character_id);

ALTER TABLE quests ADD COLUMN quest_scale TEXT NOT NULL DEFAULT 'standard';
ALTER TABLE quests ADD COLUMN chain_code TEXT;
ALTER TABLE quests ADD COLUMN chain_position INTEGER;
ALTER TABLE quests ADD COLUMN rewards_deferred INTEGER NOT NULL DEFAULT 0;

ALTER TABLE player_renown ADD COLUMN bonuses_json TEXT;
ALTER TABLE player_characters ADD COLUMN active_title_code TEXT;
