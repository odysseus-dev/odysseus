-- M2 world tables: npcs, items, quests, event_log, memories, grid
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'misc',
    rarity TEXT,
    weight REAL DEFAULT 0,
    value_gp REAL DEFAULT 0,
    description TEXT,
    stackable INTEGER NOT NULL DEFAULT 1 CHECK (stackable IN (0, 1)),
    quantity INTEGER NOT NULL DEFAULT 1,
    owner_type TEXT CHECK (owner_type IN ('player_character', 'npc', 'location', 'none')),
    owner_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS npcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    title TEXT,
    race TEXT,
    class_role TEXT,
    current_location_id INTEGER,
    portrait_asset_id INTEGER,
    portrait_path TEXT,
    portrait_prompt TEXT,
    backstory_summary TEXT,
    status TEXT NOT NULL DEFAULT 'alive',
    is_hostile INTEGER NOT NULL DEFAULT 0 CHECK (is_hostile IN (0, 1)),
    is_important INTEGER NOT NULL DEFAULT 0 CHECK (is_important IN (0, 1)),
    context_enabled INTEGER NOT NULL DEFAULT 1 CHECK (context_enabled IN (0, 1)),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (current_location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS npc_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_npc_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('player', 'player_character', 'npc', 'faction')),
    target_id INTEGER,
    attitude TEXT DEFAULT 'neutral',
    trust INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_npc_id, target_type, target_id),
    FOREIGN KEY (source_npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER,
    memory_type TEXT NOT NULL DEFAULT 'episodic',
    subject_type TEXT,
    subject_id INTEGER,
    memory_text TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 3,
    is_active INTEGER NOT NULL DEFAULT 1,
    turn_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'inactive' CHECK (status IN ('inactive', 'active', 'completed', 'failed')),
    giver_npc_id INTEGER,
    related_location_id INTEGER,
    rewards TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (giver_npc_id) REFERENCES npcs(id) ON DELETE SET NULL,
    FOREIGN KEY (related_location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS quest_objectives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quest_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    objective_type TEXT NOT NULL DEFAULT 'custom',
    description_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'complete', 'failed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (quest_id) REFERENCES quests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    title TEXT,
    summary TEXT NOT NULL,
    details_json TEXT,
    actor_type TEXT,
    actor_id INTEGER,
    target_type TEXT,
    target_id INTEGER,
    location_id INTEGER,
    turn_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    ingame_occurred_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS grid_cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    map_code TEXT NOT NULL DEFAULT 'overworld',
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    z INTEGER NOT NULL DEFAULT 0,
    location_id INTEGER,
    biome TEXT,
    is_discovered INTEGER NOT NULL DEFAULT 0 CHECK (is_discovered IN (0, 1)),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(map_code, x, y, z),
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_npcs_location ON npcs(current_location_id);
CREATE INDEX IF NOT EXISTS idx_quests_status ON quests(status);
CREATE INDEX IF NOT EXISTS idx_event_log_turn ON event_log(turn_id);
CREATE INDEX IF NOT EXISTS idx_grid_cells_coords ON grid_cells(map_code, x, y, z);
CREATE INDEX IF NOT EXISTS idx_items_owner ON items(owner_type, owner_id);

UPDATE campaign_settings SET save_version = 3 WHERE id = 1;
