-- M1 core tables for saves created with schema v1 (campaign_settings only)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    ingame_created_at TEXT,
    ingame_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description_short TEXT,
    description_long TEXT,
    region_name TEXT,
    parent_location_id INTEGER,
    image_path TEXT,
    image_prompt TEXT,
    is_discovered INTEGER NOT NULL DEFAULT 0 CHECK (is_discovered IN (0, 1)),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    ingame_created_at TEXT,
    ingame_updated_at TEXT,
    FOREIGN KEY (parent_location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS player_characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    player_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    race TEXT,
    subrace TEXT,
    class_name TEXT,
    subclass_name TEXT,
    background_name TEXT,
    alignment TEXT,
    level INTEGER NOT NULL DEFAULT 1,
    experience_points INTEGER NOT NULL DEFAULT 0,
    proficiency_bonus INTEGER NOT NULL DEFAULT 2,
    str_score INTEGER NOT NULL DEFAULT 10,
    dex_score INTEGER NOT NULL DEFAULT 10,
    con_score INTEGER NOT NULL DEFAULT 10,
    int_score INTEGER NOT NULL DEFAULT 10,
    wis_score INTEGER NOT NULL DEFAULT 10,
    cha_score INTEGER NOT NULL DEFAULT 10,
    armor_class INTEGER,
    hit_points_current INTEGER,
    hit_points_max INTEGER,
    temp_hit_points INTEGER NOT NULL DEFAULT 0,
    speed_walk INTEGER DEFAULT 30,
    passive_perception INTEGER DEFAULT 10,
    initiative_bonus INTEGER DEFAULT 0,
    spell_save_dc INTEGER,
    spell_attack_bonus INTEGER,
    current_location_id INTEGER,
    portrait_asset_id INTEGER,
    portrait_path TEXT,
    portrait_prompt TEXT,
    backstory_summary TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'dead', 'missing', 'retired', 'inactive')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    ingame_created_at TEXT,
    ingame_updated_at TEXT,
    FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE,
    FOREIGN KEY (current_location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('image', 'portrait', 'map', 'scene', 'token', 'other')),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('npc', 'player_character', 'location', 'item', 'quest', 'event', 'other')),
    entity_id INTEGER NOT NULL,
    title TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('queued', 'generating', 'ready', 'failed', 'archived')),
    prompt_source TEXT NOT NULL DEFAULT 'auto' CHECK (prompt_source IN ('auto', 'manual', 'manual_edited')),
    provider TEXT,
    model_name TEXT,
    sampler TEXT,
    steps INTEGER,
    cfg_scale REAL,
    seed INTEGER,
    width INTEGER,
    height INTEGER,
    prompt TEXT,
    negative_prompt TEXT,
    file_path TEXT,
    preview_path TEXT,
    mime_type TEXT DEFAULT 'image/png',
    source_image_path TEXT,
    metadata_json TEXT,
    created_by_type TEXT CHECK (created_by_type IN ('system', 'player', 'npc', 'gm_ai')),
    created_by_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    ingame_created_at TEXT,
    ingame_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS turn_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_number INTEGER NOT NULL DEFAULT 0,
    player_text TEXT NOT NULL DEFAULT '',
    ai_text TEXT NOT NULL DEFAULT '',
    resolution_json TEXT,
    prompt_snapshot TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ingame_time TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    replaces_turn_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_assets_entity ON assets(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_player_characters_player_id ON player_characters(player_id);
CREATE INDEX IF NOT EXISTS idx_turn_history_turn_number ON turn_history(turn_number);
CREATE INDEX IF NOT EXISTS idx_turn_history_is_active ON turn_history(is_active);

UPDATE campaign_settings SET save_version = 2 WHERE id = 1;
