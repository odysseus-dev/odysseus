-- Fugassa per-save SQLite — M1 core (ADR: assets kanon, player_characters, locations)
PRAGMA foreign_keys = ON;

-- Key/value meta (schema_version, …)
CREATE TABLE IF NOT EXISTS save_meta (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Campaign-level settings (1 row per save, id = 1)
CREATE TABLE IF NOT EXISTS campaign_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    campaign_name TEXT NOT NULL,
    campaign_length TEXT NOT NULL DEFAULT 'long',
    pacing TEXT NOT NULL DEFAULT 'balanced',
    campaign_style TEXT NOT NULL DEFAULT 'mixed',
    narrative_focus TEXT NOT NULL DEFAULT 'mixed',
    world_complexity TEXT NOT NULL DEFAULT 'medium',
    theme TEXT NOT NULL DEFAULT 'fantasy',
    world_summary TEXT,
    -- ADR §C3: simulation = Reality/Coherence Guard ON; sandbox = guards OFF.
    reality_mode TEXT NOT NULL DEFAULT 'simulation' CHECK (reality_mode IN ('simulation', 'sandbox')),
    save_version INTEGER NOT NULL DEFAULT 3,
    turn_number INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- TABLE: players (solo default — schema 1+4 later)
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

-- TABLE: locations
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

-- Sublocation graph (ADR §A) — off-grid rooms inside a location (tavern ->
-- salon -> cellar) traversed via LEADS_TO, not grid coordinates. A
-- `grid_cell_portals` row can drop the player into this graph via
-- `target_location_id`; movement within it never touches `player.{x,y,z}`.
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

-- TABLE: player_characters
CREATE TABLE IF NOT EXISTS player_characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    player_id INTEGER NOT NULL,
    -- ADR §8d party 1+4: slot 0 = hero, 1-4 = companion. M1-M5 gameplay stays
    -- solo (schema is ready ahead of the UI/generator work).
    party_slot INTEGER NOT NULL DEFAULT 0,
    party_role TEXT NOT NULL DEFAULT 'hero' CHECK (party_role IN ('hero', 'companion')),
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

-- TABLE: assets (SD kanon — ADR §L)
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

-- TABLE: turn_history (M3+ pipeline; stub in M1)
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

-- M2 world tables (see migrations/003_m2_world.sql)
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
    -- ADR §J5c travel: mount/vehicle/ship items carry a speed for the travel resolver
    speed_kmh REAL,
    item_subtype TEXT CHECK (item_subtype IS NULL OR item_subtype IN ('mount', 'vehicle', 'ship')),
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
    assigned_property_id INTEGER,
    assigned_role TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (current_location_id) REFERENCES locations(id) ON DELETE SET NULL,
    FOREIGN KEY (assigned_property_id) REFERENCES property_holdings(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS npc_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_npc_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('player', 'player_character', 'npc', 'faction')),
    target_id INTEGER,
    attitude TEXT DEFAULT 'neutral',
    trust INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    -- ADR §E knowledge boundaries: what this NPC knows about the player, separate
    -- from `attitude`/`trust` (which describe feeling once they *have* met).
    met_player INTEGER NOT NULL DEFAULT 0 CHECK (met_player IN (0, 1)),
    recognition_level TEXT NOT NULL DEFAULT 'stranger'
        CHECK (recognition_level IN ('stranger', 'rumor', 'face_only', 'acquainted', 'personal')),
    knowledge_sources TEXT,
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
    -- ADR §H8.6 reward bands / negotiation; §H8.3 fail conditions + deadline
    rewards_json TEXT,
    bonus_rewards_json TEXT,
    negotiation_rules_json TEXT,
    fail_reason TEXT,
    deadline_ingame_at TEXT,
    duration_hours INTEGER,
    activated_at_turn INTEGER,
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
    target_entity_type TEXT,
    target_entity_id INTEGER,
    target_code TEXT,
    condition_json TEXT,
    description_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'complete', 'failed')),
    optional INTEGER NOT NULL DEFAULT 0 CHECK (optional IN (0, 1)),
    completion_mode TEXT NOT NULL DEFAULT 'auto' CHECK (completion_mode IN ('auto', 'event_flag')),
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

-- M3 deep NPC + quest engine tables (ADR §B / §H)
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

-- 6 axes, -3..+3 (ADR #16: kindness, empathy, wit, drive, boldness, composure)
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

-- Mutable role tags — ADR B "hexagon != tags"
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

-- Engine-set world/quest flags for `wait_event` / `event_flag` completion (ADR H8.1)
CREATE TABLE IF NOT EXISTS world_flags (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL DEFAULT '1',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_npc_tags_npc ON npc_tags(npc_id);
CREATE INDEX IF NOT EXISTS idx_npc_skills_npc ON npc_skills(npc_id);
CREATE INDEX IF NOT EXISTS idx_quest_objectives_quest ON quest_objectives(quest_id);

-- M4 world simulation tables (ADR §B5c / §E / §E2 / §A / §4b / §J5c)
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

-- Pinned facts (§5 priority 7) — cheap, always-in-prompt truths not worth a full memory row
CREATE TABLE IF NOT EXISTS campaign_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_text TEXT NOT NULL,
    known_by TEXT,
    source_event_id INTEGER,
    pinned INTEGER NOT NULL DEFAULT 1 CHECK (pinned IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_event_id) REFERENCES event_log(id) ON DELETE SET NULL
);

-- Mid-term memory layer (§2 layer 3) — location recap on scene exit, distinct from event_log
CREATE TABLE IF NOT EXISTS scene_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER,
    summary_text TEXT NOT NULL,
    delta_text TEXT,
    turn_start INTEGER,
    turn_end INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE SET NULL
);

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

-- §4b memory retrieval graph — memories linked to the entities they're about,
-- so the context builder can pull top-K per NPC by scene relevance, not recency alone.
CREATE TABLE IF NOT EXISTS memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('subject', 'location', 'quest', 'npc', 'item', 'event')),
    entity_id INTEGER,
    link_type TEXT NOT NULL DEFAULT 'subject',
    FOREIGN KEY (memory_id) REFERENCES npc_memories(id) ON DELETE CASCADE
);

-- §A grid/portals: dungeon & multi-floor maps only transition through an explicit
-- portal cell (entrance/exit/stairs) — never by walking off the edge of a map.
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

-- §B6c region threat bands — procedural at first materialization, then locked
CREATE TABLE IF NOT EXISTS region_threat_bands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    region_code TEXT NOT NULL UNIQUE,
    threat_min REAL NOT NULL DEFAULT 1,
    threat_mid REAL NOT NULL DEFAULT 4,
    threat_max REAL NOT NULL DEFAULT 7,
    generated_at_turn INTEGER NOT NULL DEFAULT 0
);

-- §J5c active transport (mount/vehicle/ship) for the travel resolver
CREATE TABLE IF NOT EXISTS party_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active_transport_item_id INTEGER,
    active_transport_mode TEXT NOT NULL DEFAULT 'walk',
    FOREIGN KEY (active_transport_item_id) REFERENCES items(id) ON DELETE SET NULL
);

-- §7 Tier-2 campaign narrative digest — single row, grows by appending rolling
-- condensations of the oldest `turn_history` rows once the rolling window
-- (15 pairs) overflows past the 30-pair trigger. `mega_anchors_json` records
-- prior digest generations archived when the active text exceeds the cap —
-- never deleted, just no longer replayed verbatim into every prompt.
CREATE TABLE IF NOT EXISTS campaign_digest (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    digest_text TEXT NOT NULL DEFAULT '',
    mega_anchors_json TEXT NOT NULL DEFAULT '[]',
    last_condensed_turn INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Crafting system (§ crafting) — professions/ranks are gameplay-content-agnostic
-- (they enforce mechanics: hard rank gate, DC roll, ingredient consumption);
-- the actual recipes are campaign content authored at play time via the
-- "invent"/"reverse-engineer" actions, not a hardcoded catalog shipped here.
CREATE TABLE IF NOT EXISTS crafting_recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    output_item_name TEXT NOT NULL,
    output_qty INTEGER NOT NULL DEFAULT 1,
    recipe_kind TEXT NOT NULL DEFAULT 'item' CHECK (recipe_kind IN ('item', 'scroll', 'potion')),
    profession TEXT NOT NULL CHECK (
        profession IN ('weaponsmith', 'armorsmith', 'alchemist', 'enchanter', 'engineer', 'artisan')
    ),
    tier INTEGER NOT NULL DEFAULT 0 CHECK (tier BETWEEN 0 AND 5),
    min_rank INTEGER NOT NULL DEFAULT 0 CHECK (min_rank BETWEEN 0 AND 5),
    craft_dc INTEGER NOT NULL DEFAULT 10,
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    heal_amount INTEGER,
    description TEXT,
    discovered_by TEXT NOT NULL DEFAULT 'player_character',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (recipe_id) REFERENCES crafting_recipes(id) ON DELETE CASCADE
);

-- A recipe a hero actually knows (invented, reverse-engineered, handed to
-- them as a starter, or found as loot). Crafting requires owning this row —
-- knowing the ingredients isn't enough, matching the "hard rank gate" design.
CREATE TABLE IF NOT EXISTS player_blueprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hero_name TEXT NOT NULL,
    recipe_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'invented' CHECK (
        source IN ('invented', 'reverse_engineered', 'starter', 'found')
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (hero_name, recipe_id),
    FOREIGN KEY (recipe_id) REFERENCES crafting_recipes(id) ON DELETE CASCADE
);

-- Novice(0)..Grandmaster(5) rank ladder per profession. Rank is a hard
-- prerequisite gate (recipe.min_rank), not just a DC/roll modifier — a
-- Novice cannot produce a Grandmaster item even on a natural 20.
CREATE TABLE IF NOT EXISTS crafting_professions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hero_name TEXT NOT NULL,
    profession TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0 CHECK (rank BETWEEN 0 AND 5),
    xp INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (hero_name, profession)
);

CREATE INDEX IF NOT EXISTS idx_recipe_ingredients_recipe ON recipe_ingredients(recipe_id);
CREATE INDEX IF NOT EXISTS idx_player_blueprints_hero ON player_blueprints(hero_name);
CREATE INDEX IF NOT EXISTS idx_crafting_professions_hero ON crafting_professions(hero_name);

CREATE INDEX IF NOT EXISTS idx_npc_agenda_reveal ON npc_agenda(revealed_at_turn);
CREATE INDEX IF NOT EXISTS idx_player_renown_pc ON player_renown(player_character_id);
CREATE INDEX IF NOT EXISTS idx_renown_reactions_code ON renown_reactions(renown_code);
CREATE INDEX IF NOT EXISTS idx_scene_summaries_location ON scene_summaries(location_id);
CREATE INDEX IF NOT EXISTS idx_scene_turn_deltas_location_turn
    ON scene_turn_deltas(location_id, turn_number DESC);
CREATE INDEX IF NOT EXISTS idx_memory_links_memory ON memory_links(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_links_entity ON memory_links(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_grid_cell_portals_from ON grid_cell_portals(from_map_code, from_x, from_y, from_z);
CREATE INDEX IF NOT EXISTS idx_npc_relationships_recognition ON npc_relationships(recognition_level);
CREATE INDEX IF NOT EXISTS idx_location_connections_from ON location_connections(from_location_id);

CREATE TABLE IF NOT EXISTS campaign_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  save_id         TEXT NOT NULL,
  code            TEXT NOT NULL UNIQUE,
  job_type        TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  priority        INTEGER NOT NULL DEFAULT 100,
  turn_number     INTEGER,
  batch_id        TEXT NOT NULL,
  depends_on_id   INTEGER REFERENCES campaign_jobs(id),
  payload_json    TEXT,
  result_json     TEXT,
  error           TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_campaign_jobs_save_status
  ON campaign_jobs(save_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_campaign_jobs_batch
  ON campaign_jobs(batch_id, status, priority, id);

-- Property holdings (Estates domain)
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

CREATE TABLE IF NOT EXISTS property_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL UNIQUE,
    room_kind TEXT NOT NULL DEFAULT 'room',
    floor_label TEXT,
    area_sqm REAL,
    capacity_persons INTEGER,
    specs_json TEXT NOT NULL DEFAULT '{}',
    layout_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (property_id) REFERENCES property_holdings(id) ON DELETE CASCADE,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS property_fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    room_location_id INTEGER,
    item_id INTEGER,
    fixture_kind TEXT NOT NULL DEFAULT 'furniture',
    name TEXT NOT NULL,
    description TEXT,
    condition_pct INTEGER NOT NULL DEFAULT 100 CHECK (condition_pct BETWEEN 0 AND 100),
    specs_json TEXT NOT NULL DEFAULT '{}',
    installed_at_turn INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (property_id) REFERENCES property_holdings(id) ON DELETE CASCADE,
    FOREIGN KEY (room_location_id) REFERENCES locations(id) ON DELETE SET NULL,
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_property_holdings_pc ON property_holdings(player_character_id);
CREATE INDEX IF NOT EXISTS idx_property_rooms_property ON property_rooms(property_id);
CREATE INDEX IF NOT EXISTS idx_property_fixtures_property ON property_fixtures(property_id);
CREATE INDEX IF NOT EXISTS idx_property_fixtures_room ON property_fixtures(room_location_id);

INSERT OR IGNORE INTO save_meta (key, value) VALUES ('schema_version', '13');
INSERT OR IGNORE INTO party_state (id, active_transport_mode) VALUES (1, 'walk');
INSERT OR IGNORE INTO campaign_digest (id, digest_text) VALUES (1, '');

UPDATE campaign_settings SET save_version = 9 WHERE id = 1;
