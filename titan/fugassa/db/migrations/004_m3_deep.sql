-- M3 deep NPC + quest engine tables (ADR §B / §H) — upgrade path for existing saves
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS npc_stats (
    npc_id INTEGER PRIMARY KEY,
    armor_class INTEGER NOT NULL DEFAULT 10,
    hit_points_current INTEGER NOT NULL DEFAULT 10,
    hit_points_max INTEGER NOT NULL DEFAULT 10,
    speed_walk INTEGER NOT NULL DEFAULT 30,
    str_score INTEGER NOT NULL DEFAULT 10,
    dex_score INTEGER NOT NULL DEFAULT 10,
    con_score INTEGER NOT NULL DEFAULT 10,
    int_score INTEGER NOT NULL DEFAULT 10,
    wis_score INTEGER NOT NULL DEFAULT 10,
    cha_score INTEGER NOT NULL DEFAULT 10,
    passive_perception INTEGER NOT NULL DEFAULT 10,
    initiative_bonus INTEGER NOT NULL DEFAULT 0,
    attack_bonus INTEGER NOT NULL DEFAULT 2,
    damage_dice TEXT NOT NULL DEFAULT '1d6',
    challenge_rating REAL NOT NULL DEFAULT 0.25,
    tier TEXT NOT NULL DEFAULT 'T2' CHECK (tier IN ('T0', 'T1', 'T2', 'T3')),
    combat_stance TEXT NOT NULL DEFAULT 'wary' CHECK (combat_stance IN ('passive', 'wary', 'aggressive', 'flee')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    bonus INTEGER NOT NULL DEFAULT 0,
    UNIQUE(npc_id, skill_name),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_personality_hex (
    npc_id INTEGER PRIMARY KEY,
    kindness INTEGER NOT NULL DEFAULT 0 CHECK (kindness BETWEEN -3 AND 3),
    empathy INTEGER NOT NULL DEFAULT 0 CHECK (empathy BETWEEN -3 AND 3),
    wit INTEGER NOT NULL DEFAULT 0 CHECK (wit BETWEEN -3 AND 3),
    drive INTEGER NOT NULL DEFAULT 0 CHECK (drive BETWEEN -3 AND 3),
    boldness INTEGER NOT NULL DEFAULT 0 CHECK (boldness BETWEEN -3 AND 3),
    composure INTEGER NOT NULL DEFAULT 0 CHECK (composure BETWEEN -3 AND 3),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(npc_id, tag),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL,
    goal_text TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    is_secret INTEGER NOT NULL DEFAULT 0 CHECK (is_secret IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS world_flags (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL DEFAULT '1',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_npc_tags_npc ON npc_tags(npc_id);
CREATE INDEX IF NOT EXISTS idx_npc_skills_npc ON npc_skills(npc_id);
CREATE INDEX IF NOT EXISTS idx_quest_objectives_quest ON quest_objectives(quest_id);

-- quest_objectives: add H8.1 engine columns (ignore failure if already present)
ALTER TABLE quest_objectives ADD COLUMN target_entity_type TEXT;
ALTER TABLE quest_objectives ADD COLUMN target_entity_id INTEGER;
ALTER TABLE quest_objectives ADD COLUMN target_code TEXT;
ALTER TABLE quest_objectives ADD COLUMN condition_json TEXT;
ALTER TABLE quest_objectives ADD COLUMN optional INTEGER NOT NULL DEFAULT 0;
ALTER TABLE quest_objectives ADD COLUMN completion_mode TEXT NOT NULL DEFAULT 'auto';

UPDATE campaign_settings SET save_version = 4 WHERE id = 1;
