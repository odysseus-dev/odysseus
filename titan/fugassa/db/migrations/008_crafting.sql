-- Crafting system (blueprints/recipes/materials/professions) — upgrade path for existing saves
PRAGMA foreign_keys = ON;

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

UPDATE campaign_settings SET save_version = 8 WHERE id = 1;
