-- Estates v1 completion: property_rooms, property_fixtures, NPC staff assignment.

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

CREATE INDEX IF NOT EXISTS idx_property_rooms_property ON property_rooms(property_id);
CREATE INDEX IF NOT EXISTS idx_property_fixtures_property ON property_fixtures(property_id);
CREATE INDEX IF NOT EXISTS idx_property_fixtures_room ON property_fixtures(room_location_id);

-- Staff assignment on NPCs (nullable — existing rows unaffected).
-- Wrapped in migration runner tolerance for saves that already have columns from schema.sql.

-- Backfill property_rooms from existing child locations under holdings.
INSERT INTO property_rooms (property_id, location_id, room_kind, layout_notes, created_at)
SELECT h.id, l.id, 'room', COALESCE(l.description_short, ''), datetime('now')
FROM property_holdings h
JOIN locations l ON l.parent_location_id = h.root_location_id
WHERE NOT EXISTS (SELECT 1 FROM property_rooms pr WHERE pr.location_id = l.id);
