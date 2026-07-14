-- M3.5 player character sheet child tables + NPC spellbooks (ADR §B)

CREATE TABLE IF NOT EXISTS player_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_character_id INTEGER NOT NULL,
    skill_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    bonus INTEGER NOT NULL DEFAULT 0,
    proficient INTEGER NOT NULL DEFAULT 0,
    expertise INTEGER NOT NULL DEFAULT 0,
    UNIQUE(player_character_id, skill_id),
    FOREIGN KEY (player_character_id) REFERENCES player_characters(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS player_feats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_character_id INTEGER NOT NULL,
    feat_index TEXT,
    feat_name TEXT NOT NULL,
    level_gained INTEGER,
    UNIQUE(player_character_id, feat_name),
    FOREIGN KEY (player_character_id) REFERENCES player_characters(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS player_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_character_id INTEGER NOT NULL,
    feature_index TEXT,
    feature_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'class',
    level_gained INTEGER,
    UNIQUE(player_character_id, feature_index, source),
    FOREIGN KEY (player_character_id) REFERENCES player_characters(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS player_spells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_character_id INTEGER NOT NULL,
    spell_index TEXT NOT NULL,
    spell_level INTEGER NOT NULL DEFAULT 0,
    is_cantrip INTEGER NOT NULL DEFAULT 0,
    UNIQUE(player_character_id, spell_index),
    FOREIGN KEY (player_character_id) REFERENCES player_characters(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_spellbooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL,
    spell_index TEXT NOT NULL,
    spell_level INTEGER NOT NULL DEFAULT 0,
    is_cantrip INTEGER NOT NULL DEFAULT 0,
    UNIQUE(npc_id, spell_index),
    FOREIGN KEY (npc_id) REFERENCES npcs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_player_skills_pc ON player_skills(player_character_id);
CREATE INDEX IF NOT EXISTS idx_player_feats_pc ON player_feats(player_character_id);
CREATE INDEX IF NOT EXISTS idx_player_features_pc ON player_features(player_character_id);
CREATE INDEX IF NOT EXISTS idx_player_spells_pc ON player_spells(player_character_id);
CREATE INDEX IF NOT EXISTS idx_npc_spellbooks_npc ON npc_spellbooks(npc_id);

-- Optional spell combat columns on npc_stats (idempotent via PRAGMA check in app if needed;
-- SQLite ADD COLUMN is safe when column missing — migration runner runs once per version).
ALTER TABLE npc_stats ADD COLUMN spell_save_dc INTEGER;
ALTER TABLE npc_stats ADD COLUMN spell_attack_bonus INTEGER;
