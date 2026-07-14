"""Intent gate + deterministic resolvers — ADR §K / §J."""

from __future__ import annotations

import random
import re
from typing import Any

from titan.fugassa import (
    combat_engine,
    grid_engine,
    investigate_engine,
    item_engine,
    npc_agenda,
    quest_engine,
    reality_guard,
    scene_summary_engine,
    social_engine,
)
from titan.fugassa.db import state_repository
from titan.fugassa.grid_engine import move_cardinal, travel_to
from titan.fugassa.turn_resolution import TurnResolution

_TRAVEL_RE = re.compile(
    r"\b(go|travel|walk|ride|head|move)\s+(?:to\s+)?(?:cell\s+)?(-?\d+)\s*[, ]\s*(-?\d+)(?:\s*[, ]\s*(-?\d+))?",
    re.I,
)
# ADR §A: entrance/exit/stairs are a distinct, explicit action from ordinary
# cardinal movement — this is the ONLY path that may cross `grid_cell_portals`
# (change map/floor). Checked before `_DIR_RE` since "go up the stairs" would
# otherwise also match the cardinal "go up" pattern.
_PORTAL_RE = re.compile(
    r"\b(enter|go\s+(?:in|inside|into|through)|step\s+(?:in|inside|through)|"
    r"exit|go\s+outside|go\s+out\b|step\s+outside|step\s+out\b|"
    r"stairs|staircase|ascend|descend)\b",
    re.I,
)
_STAIRS_UP_HINT_RE = re.compile(r"\b(up|ascend|upstairs)\b", re.I)
_STAIRS_DOWN_HINT_RE = re.compile(r"\b(down|descend|downstairs)\b", re.I)
_EXIT_HINT_RE = re.compile(r"\b(exit|outside|out)\b", re.I)
_ENTER_HINT_RE = re.compile(r"\b(enter|in|inside|into|through)\b", re.I)
_DIR_RE = re.compile(r"\b(go|move|walk)\s+(north|south|east|west|up|down)\b", re.I)
_SEARCH_RE = re.compile(r"\b(search|investigate|look\s+for|perception)\b", re.I)
_USE_ITEM_RE = re.compile(r"\b(use|apply|drink|consume|eat|light|ignite|activate|read)\b", re.I)
_REST_RE = re.compile(r"\b(short|long)\s+rest\b|\brest\b", re.I)
_SOCIAL_RE = re.compile(r"\b(talk|speak|persuade|convince|ask|negotiate|bribe|intimidate)\b", re.I)
_COMBAT_RE = re.compile(r"\b(attack|strike|cast|shoot|spare|dodge)\b", re.I)
_SUBLOC_MOVE_RE = re.compile(r"\b(go|head|walk|move)\s+(?:to|towards|into)\s+(?:the\s+)?([a-zA-Z][\w\s]{1,30})\b", re.I)
_GO_HOME_RE = re.compile(
    r"\b(?:go\s+home|return\s+home|head\s+home|back\s+home|"
    r"return\s+to\s+(?:my\s+)?(?:home|residence|house|townhouse)|"
    r"go\s+to\s+(?:my\s+)?(?:home|residence|house|townhouse))\b",
    re.I,
)
_NARRATIVE_TRAVEL_RE = re.compile(
    r"\b(?:visit|go\s+to|head\s+to|make\s+(?:my|your)\s+way|walk\s+to|travel\s+to|"
    r"approach|step\s+into|go\s+inside|go\s+into|proceed\s+to|move\s+to|make\s+for)\b",
    re.I,
)


def classify_intent(text: str) -> str:
    action = str(text or "").strip()
    if not action:
        return "idle"
    if _TRAVEL_RE.search(action):
        return "travel"
    if _PORTAL_RE.search(action):
        return "portal"
    if _DIR_RE.search(action):
        return "move"
    if _SEARCH_RE.search(action):
        return "search"
    if _USE_ITEM_RE.search(action):
        return "use_item"
    if _SOCIAL_RE.search(action):
        return "social"
    if _REST_RE.search(action):
        return "rest"
    if _COMBAT_RE.search(action):
        return "combat"
    if _GO_HOME_RE.search(action):
        return "narrative_travel"
    if _NARRATIVE_TRAVEL_RE.search(action):
        return "narrative_travel"
    return "narrative_only"


def resolve_turn(state: dict[str, Any], player_text: str, db_path: str | None = None) -> TurnResolution:
    intent = classify_intent(player_text)
    resolution = TurnResolution(mode="action", intent=intent, actor=state.get("player_character_id"))

    try:
        guard_result = reality_guard.evaluate(player_text, state, db_path)
    except Exception:  # noqa: BLE001 — guard must never break the turn pipeline
        guard_result = {}
    if guard_result:
        resolution.guard = guard_result
        if guard_result.get("verdict") == "reject":
            resolution.gm_instruction = (
                f"REALITY GUARD REJECTED this player line ({guard_result.get('classification')}): "
                f"{guard_result.get('reason')} {guard_result.get('reframe_hint')}"
            ).strip()

    if intent == "travel":
        m = _TRAVEL_RE.search(player_text)
        if m:
            x, y = int(m.group(2)), int(m.group(3))
            z = int(m.group(4)) if m.group(4) else int((state.get("player") or {}).get("z", 0))
            player = state.get("player") or {}
            ox, oy = int(player.get("x", 0)), int(player.get("y", 0))
            # ADR §J5c: "Chat (jedu do...) může iniciovat travel; režim
            # dopravy engine bere z mapy / active_transport, ne z volného
            # textu" — the verb in `player_text` (walk/ride/...) never picks
            # the mode, only the party's currently active transport does.
            mode = grid_engine.current_transport(db_path)["mode"]
            msg = travel_to(state, x, y, z, mode, db_path=db_path, increment_turn=False)
            resolution.travel = {
                "x": x,
                "y": y,
                "z": z,
                "mode": mode,
                "summary": msg,
            }
            if msg.startswith("Traveled"):
                cost = grid_engine.travel_cost(db_path, (ox, oy), (x, y))
                resolution.time_delta_minutes = cost["time_delta_minutes"]
                resolution.travel["distance_km"] = cost["distance_km"]
                resolution.travel["speed_kmh"] = cost["speed_kmh"]
                sync_location_and_track(db_path, state, resolution)

    elif intent == "move":
        m = _DIR_RE.search(player_text)
        if m:
            direction = m.group(2).lower()
            deltas = {
                "north": (0, -1, 0),
                "south": (0, 1, 0),
                "east": (1, 0, 0),
                "west": (-1, 0, 0),
                "up": (0, 0, 1),
                "down": (0, 0, -1),
            }
            d = deltas.get(direction, (0, 0, 0))
            player = state.get("player") or {}
            ox, oy = int(player.get("x", 0)), int(player.get("y", 0))
            msg = move_cardinal(state, d[0], d[1], d[2], db_path=db_path, increment_turn=False)
            new_player = state.get("player") or {}
            nx, ny = int(new_player.get("x", ox)), int(new_player.get("y", oy))
            resolution.travel = {"direction": direction, "summary": msg}
            if msg.startswith("Traveled"):
                cost = grid_engine.travel_cost(db_path, (ox, oy), (nx, ny))
                resolution.time_delta_minutes = cost["time_delta_minutes"]
                resolution.travel["mode"] = cost["mode"]
                resolution.travel["distance_km"] = cost["distance_km"]
                resolution.travel["speed_kmh"] = cost["speed_kmh"]
                sync_location_and_track(db_path, state, resolution)

    elif intent == "portal":
        hint = None
        if _STAIRS_UP_HINT_RE.search(player_text) and not _STAIRS_DOWN_HINT_RE.search(player_text):
            hint = "stairs_up"
        elif _STAIRS_DOWN_HINT_RE.search(player_text):
            hint = "stairs_down"
        elif _EXIT_HINT_RE.search(player_text):
            hint = "exit"
        elif _ENTER_HINT_RE.search(player_text):
            hint = "entrance"

        player_now = state.get("player") or {}
        if player_now.get("sublocation_id") and hint == "exit":
            result = grid_engine.leave_sublocation(db_path, state)
        else:
            result = grid_engine.use_portal(db_path, state, portal_type_hint=hint)
        resolution.travel = result
        if result.get("success"):
            resolution.time_delta_minutes = 5
            sync_location_and_track(db_path, state, resolution)

    elif intent == "narrative_travel":
        from titan.fugassa import narrative_movement

        result = narrative_movement.resolve_narrative_travel(db_path, state, player_text)
        if result and result.get("success") and result.get("reason") != "already_there":
            resolution.mode = "action"
            resolution.intent = "narrative_travel"
            resolution.travel = result
            resolution.time_delta_minutes = int(result.get("time_delta_minutes") or 8)
            sync_location_and_track(db_path, state, resolution)
        else:
            from titan.fugassa import world_time_engine

            resolution.mode = "narrative_only"
            resolution.intent = "narrative_travel"
            resolution.time_delta_minutes = world_time_engine.default_narrative_minutes("narrative_travel")
            resolution.binding_summary = "Travel intent noted — GM will narrate if destination is not mapped yet"

    elif intent == "narrative_only" and (state.get("player") or {}).get("sublocation_id") and _SUBLOC_MOVE_RE.search(player_text or ""):
        # Off-grid rooms have no coordinates to travel toward — a player
        # inside a sublocation saying "go to the cellar" reads as graph
        # traversal (`location_connections`), not a grid move.
        m = _SUBLOC_MOVE_RE.search(player_text)
        result = grid_engine.move_sublocation(db_path, state, m.group(2) if m else "")
        if result.get("success"):
            resolution.travel = result
            resolution.mode = "action"
            resolution.intent = "portal"
            resolution.time_delta_minutes = 3
            sync_location_and_track(db_path, state, resolution)
        else:
            resolution.mode = "narrative_only"
            resolution.intent = "narrative_only"
            resolution.binding_summary = "Pure dialog — no mechanical changes"

    elif intent == "search":
        turn = int(state.get("turn") or 0)
        # A plain "search"/"investigate" chat line has no explicit type
        # selection (that's what the dedicated popup is for — see
        # `game_session.investigate`), so it broadly checks everything at
        # once; investigate_engine's per-(location, type) permanent
        # exhaustion still applies, so repeating "I search the room" is not
        # a free re-roll once a type has already been resolved here.
        search_result = investigate_engine.resolve_investigate(
            db_path, state, list(investigate_engine.SEARCH_TYPES), investigate_engine.DEFAULT_DURATION_MINUTES
        )
        resolution.search = {
            "results": search_result["results"],
            "revealed_any": search_result["revealed_any"],
            "summary": search_result["summary"],
        }
        resolution.time_delta_minutes = search_result["time_delta_minutes"] or investigate_engine.DEFAULT_DURATION_MINUTES
        # ADR §B5c: "investigate"/"search" doubles as the Investigation-skill
        # reveal path for any present NPC's hidden agenda (Insight lives on
        # the social/persuasion path in resolve_social's dialog rolls). This
        # is a distinct roll from the item/hidden/npc/enemy search above.
        agenda_roll = random.randint(1, 20)
        _attempt_agenda_reveal_via_search(db_path, state, resolution, player_text, agenda_roll, turn)

    elif intent == "use_item":
        resolution.inventory = item_engine.resolve_use_item(db_path, state, player_text)
        resolution.time_delta_minutes = 2

    elif intent == "social":
        from titan.fugassa import currency_engine

        bribe = currency_engine.parse_bribe_from_text(player_text, state)
        if bribe and bribe.get("spent"):
            currency_engine.record_currency_delta(
                resolution,
                {
                    "applied": -int(bribe["spent"]),
                    "tier": bribe.get("tier"),
                    "reason": "social_bribe",
                },
            )
        resolution.social = social_engine.resolve_social(db_path, state, player_text)
        if bribe:
            resolution.social["bribe"] = bribe
        resolution.time_delta_minutes = 5

    elif intent == "rest":
        resolution.sheet_delta = {"rest": "short", "summary": "Short rest taken"}
        resolution.time_delta_minutes = 60

    elif intent == "combat":
        if not state.get("in_combat"):
            start = combat_engine.start_combat(db_path, state)
            resolution.combat = {"in_combat": True, "started": True, "order": start.get("order", [])}
        attack = combat_engine.resolve_player_attack(db_path, state, player_text)
        resolution.combat.update(attack)
        counter = combat_engine.resolve_npc_counterattacks(db_path, state)
        if counter:
            resolution.combat["counterattacks"] = counter
        resolution.combat["in_combat"] = bool(state.get("in_combat"))
        resolution.time_delta_minutes = 6

    else:
        from titan.fugassa import world_time_engine

        resolution.mode = "narrative_only"
        resolution.intent = "narrative_only"
        resolution.time_delta_minutes = world_time_engine.default_narrative_minutes("narrative_only")
        resolution.binding_summary = "Pure dialog — no mechanical changes"

    # ADR §B5c: sweep betrayal triggers for NPCs in the current scene — a
    # reveal here can flip `combat_stance` to aggressive, which the combat
    # auto-trigger check right below picks up in the very same turn (ambush).
    try:
        agenda_result = npc_agenda.evaluate_scene_agendas(db_path, state)
    except Exception:  # noqa: BLE001 — agenda sweep must never break the turn pipeline
        agenda_result = {}
    if agenda_result.get("revealed"):
        resolution.agenda["revealed"] = agenda_result["revealed"]
    if agenda_result.get("secret_gm_notes"):
        resolution.secret_gm_notes = agenda_result["secret_gm_notes"]

    if intent != "combat":
        trigger = combat_engine.evaluate_combat_trigger(db_path, state)
        if trigger:
            combat_engine.start_combat(db_path, state)
            resolution.mode = "action"
            resolution.combat = trigger
            resolution.combat["in_combat"] = True

    _run_quest_engine(db_path, state, resolution)
    from titan.fugassa.currency_engine import apply_resolution_currency

    apply_resolution_currency(state, resolution)
    return resolution


def _run_quest_engine(db_path: str | None, state: dict[str, Any], resolution: TurnResolution) -> None:
    try:
        result = quest_engine.evaluate_quests(db_path, state, resolution)
        quest_engine.record_quest_chronicle(db_path, result, turn_resolution=resolution)
    except Exception:  # noqa: BLE001 — quest eval must never break the turn pipeline
        pass


def run_engine_only_checks(
    db_path: str | None,
    state: dict[str, Any],
    resolution: TurnResolution | None = None,
) -> TurnResolution:
    """Quest/combat resolvers for HUD actions outside the chat pipeline.

    ADR §K3 / §J3: `move`/`travel`/loot via the Map or HUD are `engine_only`
    turns — no GM/archivist call, but step-1 resolvers (quest completion,
    combat auto-trigger) still must run against the fresh SQL state, exactly
    like the chat-driven path. Call this *after* the caller has already
    mutated `state` and synced location/inventory to SQL.
    """
    resolution = resolution or TurnResolution(
        mode="engine_only", intent="engine_only", actor=state.get("player_character_id")
    )
    try:
        trigger = combat_engine.evaluate_combat_trigger(db_path, state)
    except Exception:  # noqa: BLE001
        trigger = None
    if trigger:
        combat_engine.start_combat(db_path, state)
        resolution.combat = trigger
        resolution.combat["in_combat"] = True
    _run_quest_engine(db_path, state, resolution)
    return resolution


def _attempt_agenda_reveal_via_search(
    db_path: str | None, state: dict[str, Any], resolution: TurnResolution, player_text: str, roll: int, turn: int
) -> None:
    if not db_path:
        return
    import os
    import sqlite3

    if not os.path.isfile(db_path):
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        loc_row = conn.execute(
            "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        loc_id = int(loc_row["current_location_id"]) if loc_row and loc_row["current_location_id"] else None
        if not loc_id:
            return
        npc = social_engine._find_target_npc(conn, loc_id, player_text)
        if not npc:
            return
        revealed = npc_agenda.attempt_reveal_via_check_conn(conn, npc["id"], skill="investigation", roll=roll, turn=turn)
        if revealed:
            conn.commit()
            resolution.agenda["revealed"] = resolution.agenda.get("revealed", []) + [revealed]
    finally:
        conn.close()


def apply_time_delta(state: dict[str, Any], minutes: int) -> None:
    from titan.fugassa import world_time_engine

    world_time_engine.apply_time_delta(state, minutes)


def sync_location_and_track(db_path: str | None, state: dict[str, Any], resolution: TurnResolution) -> int | None:
    """Sync SQL location-of-record, then — ADR §7 `scene_summaries` — roll up
    the location the player is leaving before moving on.

    Tracked via the dedicated `_current_location_id` key, NOT
    `location_state["location_id"]` — `move_cardinal`/`travel_to`/`use_portal`
    already replaced `location_state` with the freshly generated destination
    scene blob (name/npcs/loot/...) by the time this runs, so the old
    location id would already be gone from there.
    """
    prev_loc = state.get("_current_location_id")
    prev_loc_id = int(prev_loc) if prev_loc else None
    loc_id = state_repository.sync_location_only(db_path, state) if db_path else None
    if loc_id and prev_loc_id and loc_id != prev_loc_id:
        turn = int(state.get("turn") or 0)
        scene_summary_engine.generate_on_location_exit(db_path, state, from_location_id=prev_loc_id, turn_end=turn)
    if loc_id:
        state["_current_location_id"] = loc_id
        loc = dict(state.get("location_state") or {})
        loc["location_id"] = loc_id
        state["location_state"] = loc
        scene_summary_engine.mark_location_entered(state, loc_id, int(state.get("turn") or 0))
    _maybe_enqueue_scene(resolution, state, loc_id, db_path=db_path)
    if loc_id and (not prev_loc_id or int(loc_id) != int(prev_loc_id)):
        _maybe_enqueue_population(db_path, state, loc_id)
    return loc_id


def _maybe_enqueue_population(
    db_path: str | None,
    state: dict[str, Any],
    location_id: int | None,
    *,
    opening_excerpt: str = "",
) -> None:
    if not db_path or not location_id:
        return
    from titan.fugassa import campaign_job_runner, location_population_engine
    from titan.fugassa.save_store import save_id_from_db_path

    save_id = save_id_from_db_path(db_path)
    if not save_id:
        return
    job_id = location_population_engine.enqueue_population_job(
        db_path,
        save_id=save_id,
        location_id=int(location_id),
        state=state,
        opening_excerpt=opening_excerpt,
        turn_number=int(state.get("turn") or 0),
    )
    if job_id:
        campaign_job_runner.ensure_worker_scheduled(save_id, db_path)


def _maybe_enqueue_scene(
    resolution: TurnResolution,
    state: dict[str, Any],
    location_id: int | None,
    *,
    db_path: str | None = None,
) -> None:
    if not location_id:
        return
    loc = state.get("location_state") or {}
    if loc.get("_scene_asset_queued") == location_id:
        if db_path and _location_has_scene_image(db_path, location_id):
            return
        # Flag set but no image on disk/SQL yet (failed SD, preempted job,
        # or opening bootstrap before this path existed) — allow a re-queue.
    loc["_scene_asset_queued"] = location_id
    state["location_state"] = loc
    resolution.asset_requests.append(
        {
            "asset_type": "scene",
            "entity_type": "location",
            "entity_id": location_id,
            "reason": "first_discovery",
            "prompt_seed": {
                "name": loc.get("name", "Unknown"),
                "description": loc.get("description", ""),
            },
        }
    )


def _location_has_scene_image(db_path: str, location_id: int) -> bool:
    import os
    import sqlite3

    if not db_path or not os.path.isfile(db_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT image_path FROM locations WHERE id = ?", (location_id,)).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def enqueue_opening_scene(
    db_path: str | None,
    state: dict[str, Any],
    resolution: TurnResolution,
    *,
    turn: int,
) -> int | None:
    """First scene at game start / opening bootstrap — no location-exit summary."""
    if not db_path:
        return None
    loc_id = state_repository.sync_location_only(db_path, state)
    if not loc_id:
        return None
    state["_current_location_id"] = loc_id
    loc = dict(state.get("location_state") or {})
    loc["location_id"] = loc_id
    state["location_state"] = loc
    scene_summary_engine.mark_location_entered(state, loc_id, turn)
    _maybe_enqueue_scene(resolution, state, loc_id, db_path=db_path)
    return loc_id
