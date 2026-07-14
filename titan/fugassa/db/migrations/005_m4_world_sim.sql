-- M4 world simulation — upgrade path for existing saves (ADR §B5c/§C3/§E/§E2/§A/§4b/§H8.3/§H8.6/§8d/§J5c)
PRAGMA foreign_keys = ON;

-- npc_relationships: knowledge boundaries (§E)
ALTER TABLE npc_relationships ADD COLUMN met_player INTEGER NOT NULL DEFAULT 0;
ALTER TABLE npc_relationships ADD COLUMN recognition_level TEXT NOT NULL DEFAULT 'stranger';
ALTER TABLE npc_relationships ADD COLUMN knowledge_sources TEXT;

-- quests: rewards + fail conditions (§H8.3 / §H8.6)
ALTER TABLE quests ADD COLUMN rewards_json TEXT;
ALTER TABLE quests ADD COLUMN bonus_rewards_json TEXT;
ALTER TABLE quests ADD COLUMN negotiation_rules_json TEXT;
ALTER TABLE quests ADD COLUMN fail_reason TEXT;
ALTER TABLE quests ADD COLUMN deadline_ingame_at TEXT;
ALTER TABLE quests ADD COLUMN duration_hours INTEGER;
ALTER TABLE quests ADD COLUMN activated_at_turn INTEGER;

-- player_characters: party 1+4 (§8d)
ALTER TABLE player_characters ADD COLUMN party_slot INTEGER NOT NULL DEFAULT 0;
ALTER TABLE player_characters ADD COLUMN party_role TEXT NOT NULL DEFAULT 'hero';

-- items: transport (§J5c)
ALTER TABLE items ADD COLUMN speed_kmh REAL;
ALTER TABLE items ADD COLUMN item_subtype TEXT;

-- campaign_settings: reality mode (§C3)
ALTER TABLE campaign_settings ADD COLUMN reality_mode TEXT NOT NULL DEFAULT 'simulation';

CREATE TABLE IF NOT EXISTS npc_agenda (
    npc_id INTEGER PRIMARY KEY,
    public_disposition TEXT NOT NULL DEFAULT 'neutral',
    secret_disposition TEXT,
    agenda_code TEXT,
    reveal_condition TEXT,
    betrayal_trigger_json TEXT,
    revealed_at_turn INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS player_renown (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_character_id INTEGER NOT NULL,
    renown_code TEXT NOT NULL,
    title_display TEXT,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('faction', 'region', 'global')),
    scope_id TEXT,
    valence TEXT NOT NULL DEFAULT 'positive' CHECK (valence IN ('positive', 'negative', 'mixed')),
    impact_tier INTEGER NOT NULL DEFAULT 1 CHECK (impact_tier BETWEEN 1 AND 4),
    memory_duration TEXT NOT NULL DEFAULT 'arc' CHECK (memory_duration IN ('ephemeral', 'arc', 'permanent')),
    source_event_id INTEGER,
    granted_at_turn INTEGER NOT NULL DEFAULT 0,
    in_game_day INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (player_character_id) REFERENCES player_characters(id) ON DELETE CASCADE,
    FOREIGN KEY (source_event_id) REFERENCES event_log(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS renown_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    renown_code TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('faction', 'region', 'default_stranger')),
    target_id TEXT,
    reaction TEXT NOT NULL CHECK (reaction IN ('positive', 'negative', 'indifferent', 'wary')),
    disposition_modifier INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS campaign_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_text TEXT NOT NULL,
    known_by TEXT,
    source_event_id INTEGER,
    pinned INTEGER NOT NULL DEFAULT 1 CHECK (pinned IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_event_id) REFERENCES event_log(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS scene_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER,
    summary_text TEXT NOT NULL,
    turn_start INTEGER,
    turn_end INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('subject', 'location', 'quest', 'npc', 'item', 'event')),
    entity_id INTEGER,
    link_type TEXT NOT NULL DEFAULT 'subject',
    FOREIGN KEY (memory_id) REFERENCES npc_memories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS grid_maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    map_type TEXT NOT NULL DEFAULT 'overworld' CHECK (map_type IN ('overworld', 'dungeon', 'cave', 'interior')),
    name TEXT,
    parent_location_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (parent_location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS grid_cell_portals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_map_code TEXT NOT NULL,
    from_x INTEGER NOT NULL,
    from_y INTEGER NOT NULL,
    from_z INTEGER NOT NULL DEFAULT 0,
    portal_type TEXT NOT NULL CHECK (portal_type IN ('entrance', 'exit', 'stairs_up', 'stairs_down', 'door')),
    target_map_code TEXT,
    target_x INTEGER,
    target_y INTEGER,
    target_z INTEGER DEFAULT 0,
    target_location_id INTEGER,
    is_locked INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0, 1)),
    lock_reason TEXT,
    label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_map_code, from_x, from_y, from_z),
    FOREIGN KEY (target_location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS region_threat_bands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region_code TEXT NOT NULL UNIQUE,
    threat_min REAL NOT NULL DEFAULT 1,
    threat_mid REAL NOT NULL DEFAULT 4,
    threat_max REAL NOT NULL DEFAULT 7,
    generated_at_turn INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS party_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active_transport_item_id INTEGER,
    active_transport_mode TEXT NOT NULL DEFAULT 'walk',
    FOREIGN KEY (active_transport_item_id) REFERENCES items(id) ON DELETE SET NULL
);

INSERT OR IGNORE INTO party_state (id, active_transport_mode) VALUES (1, 'walk');

CREATE INDEX IF NOT EXISTS idx_npc_agenda_reveal ON npc_agenda(revealed_at_turn);
CREATE INDEX IF NOT EXISTS idx_player_renown_pc ON player_renown(player_character_id);
CREATE INDEX IF NOT EXISTS idx_renown_reactions_code ON renown_reactions(renown_code);
CREATE INDEX IF NOT EXISTS idx_scene_summaries_location ON scene_summaries(location_id);
CREATE INDEX IF NOT EXISTS idx_memory_links_memory ON memory_links(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_links_entity ON memory_links(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_grid_cell_portals_from ON grid_cell_portals(from_map_code, from_x, from_y, from_z);
CREATE INDEX IF NOT EXISTS idx_npc_relationships_recognition ON npc_relationships(recognition_level);

UPDATE campaign_settings SET save_version = 5 WHERE id = 1;
