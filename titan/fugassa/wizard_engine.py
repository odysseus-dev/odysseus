"""Full Python port of Fugassa-II backend/wizardEngine.js."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from titan.fugassa import wizard_json as wj
from titan.fugassa.llm_client import FugassaLlmDisabled, wizard_chat

DEFAULT_RETRIES = 2
RETRY_DELAY_MS = 500

WIZARD_JSON_OUTPUT_RULES = (
    "Emit only valid JSON for the schema. No markdown fences, no commentary, "
    "no placeholder tokens like [lb]."
)

INVENTORY_EXCLUDE_WEAPON_ARMOR = (
    "SCOPE (critical): Do not include primary weapons, firearms, melee weapons, "
    "worn armor, shields, or power armor in this inventory—those are created in "
    "the separate Gear wizard step. Prefer consumables, tools, gadgets, exploration "
    "gear, personal items, medical supplies, and non-weapon equipment. usage values "
    "should reflect that (e.g. utility, exploration, consumable, tech)—not primary "
    "weapon or worn armor."
)

BACKSTORY_ANCHOR_GUIDANCE = (
    "BACKSTORY ANCHOR (critical): Read the character backstory in the wizard context. "
    "When it names specific carried items, tools, keepsakes, pouches, medical gear, "
    "or personal props, at least one of the 3 options MUST include those named pieces "
    "(or a close mundane equivalent). Options 2–3 may offer practical alternates, but "
    "do not ignore obvious backstory kit. Do not invent legendary loot not implied by "
    "the backstory."
)

BACKSTORY_GEAR_ANCHOR_GUIDANCE = (
    "BACKSTORY GEAR ANCHOR (critical): Read the character backstory in the wizard context. "
    "When it names a specific weapon, sidearm, blade, bow, armor, coat, uniform, or "
    "shield the character is known to carry or wear, at least one of the 3 loadouts MUST "
    "use those named pieces (or a close mundane equivalent with sensible dice/AC). "
    "Other options may vary practical alternates. Do not ignore an explicit backstory "
    "weapon or armor in favor of generic filler."
)

CURRENCY_GUIDANCE = (
    "CAMPAIGN CURRENCY (critical): Each inventory option must include currency — exactly "
    "three short tiered names (low / mid / high) appropriate to the genre and world "
    "(e.g. bronze/silver/gold, credits/data chips/reactor cores). Conversion is always "
    "100 low = 1 mid and 100 mid = 1 high. Names must be consistent within an option."
)

STARTER_INVENTORY_GUIDANCE = (
    "STARTER INVENTORY (critical): Day-one supplies a traveler actually carries — "
    "waterskin, rations, rope, tinderbox, bedroll, simple tools, minor medical kit, "
    "travel cloak. Prefer common items; 3–6 items per option with realistic quantities "
    "(1–5). Avoid rare legendary luxuries unless the character background explicitly "
    "justifies one modest personal keepsake."
)

STARTER_GEAR_GUIDANCE = (
    "STARTER GEAR (critical): Day-one equipment for a new campaign — practical, common, "
    "replaceable kit, not endgame loot. Weapons: simple sword, mace, staff, dagger, bow, "
    "spear, club — mundane names, no artifact titles. Armor: cloth, leather, padded, "
    "studded leather, chain shirt — AC typically 11–14 at level 1. special_effects should "
    "be empty or one minor flavor tag only; no powerful enchantments unless background "
    "clearly warrants a single modest item.\n"
    "WEAPON DAMAGE (critical): weapon.damage MUST be tabletop dice notation only — "
    "e.g. 1d4, 1d6, 1d8, 1d10, 2d6, 1d6+2. Never flat numbers (12, dmg 12, damage 8) "
    "and never use AC or attack_bonus as damage. attack_bonus is a separate integer field."
)


def _max_tokens(llm_config: dict[str, Any] | None, default: int) -> int:
    config = llm_config if isinstance(llm_config, dict) else {}
    raw = config.get("max_tokens", default)
    try:
        return max(int(raw), default)
    except (TypeError, ValueError):
        return default


def _dialog_text(dialog_transcript: str | list[dict[str, str]] | None) -> str:
    return wj.dialog_transcript(dialog_transcript)


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


async def _wizard_call(
    messages: list[dict[str, str]],
    *,
    llm_config: dict[str, Any] | None,
    owner: str | None,
    llm_enabled: bool,
    default_max_tokens: int,
) -> str:
    return await wizard_chat(
        messages,
        owner=owner,
        llm_enabled=llm_enabled,
        max_tokens=_max_tokens(llm_config, default_max_tokens),
    )


async def generateWorldOptions(
    theme: str,
    campaign_length: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    player_request: str = "",
    option_start: int = 1,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    opt_s = wj.clamp_option_start(option_start)
    n1, n2, n3 = opt_s, opt_s + 1, opt_s + 2

    system_prompt = f"""You are an RPG world creation assistant. Generate exactly 3 distinct campaign proposals.

Output MUST be valid JSON matching this shape only:
{{"campaigns":[{{"title":"...","paragraph_1":"...","paragraph_2":"..."}},{{...}},{{...}}],"selection_hint":"..."}}

IMPORTANT SCOPE:
- Only propose WORLD information (setting, atmosphere, factions, conflict, lore).
- Do NOT include character build suggestions (class/subclass/stats/race).
- Do NOT include starting location details, spawn points, or opening quest specifics.
- If such details are relevant, leave them for later tabs (Backstory/Character/Opening).

DIVERSITY (critical):
- The 3 proposals must feel clearly different: vary at least two of — core conflict type, geographic or social scale, subgenre flavor, and central image or metaphor.
- If the player asked for new or different options, do not recycle prior proposal titles, city names, or plot beats from earlier messages; invent a fresh batch.
- When the genre tag is broad (e.g. modern / sci-fi), avoid three near-identical cyberpunk megacities; stretch it into distinct angles.

{WIZARD_JSON_OUTPUT_RULES}"""

    hint = str(player_request or "").strip()
    extra = f"\nPlayer request (follow literally):\n{hint}\n" if hint else ""

    user_prompt = (
        f"Genre tag: {theme}\n"
        f"Campaign length: {campaign_length}\n"
        f"{wj.build_rules_context_block(rules_context)}{extra}"
        "Generate 3 distinct campaign proposals. Each should have:\n"
        "- title: Short evocative title\n"
        "- paragraph_1: First descriptive paragraph\n"
        "- paragraph_2: Second descriptive paragraph\n\n"
        f'The player-facing formatted output will label these campaigns "Campaign {n1}:", '
        f'"Campaign {n2}:", and "Campaign {n3}:" in that order (do not put those labels '
        "inside JSON fields).\n\n"
        f'End with a selection_hint like "Choose one campaign number ({wj.selection_hint_triple(opt_s)}), '
        'send your own world concept, or ask for different proposals."'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    last_raw = ""

    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)

            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=7000,
            )

            json_data = wj.parse_wizard_json_object(last_raw)
            campaigns = json_data.get("campaigns") if isinstance(json_data, dict) else None
            if isinstance(campaigns, list) and len(campaigns) == 3:
                formatted = []
                for idx, campaign in enumerate(campaigns):
                    formatted.append(
                        f"Campaign {opt_s + idx}: {campaign.get('title', '')}\n"
                        f"{campaign.get('paragraph_1', '')}\n\n"
                        f"{campaign.get('paragraph_2', '')}"
                    )
                formatted.append(
                    f"Choose one campaign number ({wj.selection_hint_triple(opt_s)}), send your own world concept, or ask for different proposals."
                )
                return {
                    "text": "\n\n".join(formatted).strip(),
                    "raw": last_raw,
                    "valid": True,
                    "campaigns": campaigns,
                    "optionStartUsed": opt_s,
                }

            extracted = wj.extract_answer_text(last_raw)
            if extracted and wj.prose_has_three_campaigns_at(extracted, opt_s):
                return {
                    "text": extracted,
                    "raw": last_raw,
                    "valid": True,
                    "campaigns": None,
                    "optionStartUsed": opt_s,
                }

            renumbered = wj.renumber_world_campaign_batch_text(extracted or last_raw, opt_s)
            if renumbered and wj.prose_has_three_campaigns_at(renumbered, opt_s):
                return {
                    "text": renumbered,
                    "raw": last_raw,
                    "valid": True,
                    "campaigns": None,
                    "optionStartUsed": opt_s,
                }

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response format was incorrect. Retry with valid JSON only "
                            "(no prose, no [lb] placeholders). Exactly 3 campaigns with title, "
                            "paragraph_1, paragraph_2, plus selection_hint."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    fallback = wj.extract_answer_text(last_raw) or last_raw
    renorm = wj.renumber_world_campaign_batch_text(fallback, opt_s)
    final_text = renorm or fallback
    return {
        "text": final_text,
        "raw": last_raw,
        "valid": wj.prose_has_three_campaigns_at(final_text, opt_s),
        "error": str(last_error) if last_error else None,
        "optionStartUsed": opt_s,
    }


async def generateBackstoryOptions(
    theme: str,
    player_name: str,
    world_information: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    options_hint: str = "",
    character_profile: str = "",
    option_start: int = 1,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    opt_s = wj.clamp_option_start(option_start)
    n1, n2, n3 = opt_s, opt_s + 1, opt_s + 2

    system_prompt = f"""You are an RPG character creation assistant. Generate exactly 3 distinct character backstory proposals.

Output format MUST be valid JSON matching this shape only:
{{"options":[{{"title":"...","paragraph_1":"...","paragraph_2":"..."}},{{...}},{{...}}],"selection_hint":"..."}}

CRITICAL FOR THE PLAYER-FACING REPLY:
- The world/setting text is provided only as hidden context. NEVER repeat it, summarize it as a wall of text, paste headings like "World Name" or "Setting Overview", or dump factions/geography/magic systems into your answer.
- Your answer must contain ONLY: three labeled backstory options (via JSON fields) plus a short selection_hint. Each option is a few paragraphs about the NAMED PLAYER CHARACTER, grounded in the setting.

WORLD ADHERENCE (hard rules — same priority as JSON validity):
- INTERNAL WORLD CONTEXT is canon for era, geography, technology level, society, and whether magic/supernatural elements exist.
- If the world describes mundane modern/historical/contemporary life and says there is no magic (or implies real-world physics only), your options MUST NOT add spellcasting, enchanted items, guilds of mages, monsters, planar travel, or other stock fantasy tropes.
- If the world is sci-fi, cyberpunk, or historical, match that — do not default to generic D&D fantasy.
- When world context is "(not provided)" or very thin, infer cautiously from the genre tag only; avoid contradicting it (e.g. Modern / Slice of Life → no wizards).

{WIZARD_JSON_OUTPUT_RULES}"""

    raw_world = wj.strip_ui_markers(str(world_information or "").strip())
    world_block = raw_world or "(not provided)"
    hint_block = (
        f"\nAdditional direction from player (optional): {options_hint}\n" if options_hint else ""
    )
    profile_text = wj.strip_ui_markers(str(character_profile or "").strip())
    profile_block = (
        "Fixed character sheet facts (all three options MUST respect these; do not "
        f"contradict race, class, gender, or age):\n{profile_text}\n\n"
        if profile_text
        else ""
    )

    world_excerpt = (
        f"{world_block[:20000]}\n[...truncated]" if len(world_block) > 20000 else world_block
    )

    user_prompt = (
        f"Genre tag: {theme}\n"
        f"Player name: {player_name or 'Unnamed hero'}\n"
        f"{profile_block}{wj.build_rules_context_block(rules_context)}\n\n"
        "INTERNAL WORLD CONTEXT (do not output this block; use only to stay consistent — obey its facts about magic, era, and place):\n"
        f"{world_excerpt}{hint_block}"
        "Generate exactly 3 distinct playable backstory options for this player character in this world.\n"
        "Each option must have:\n"
        "- title: Short evocative title\n"
        "- paragraph_1: First descriptive paragraph (character-focused)\n"
        "- paragraph_2: Second descriptive paragraph (character-focused)\n\n"
        "All three options must be mutually distinct but equally compliant with the world rules above.\n"
        f'Do not restate the world bible. The formatted player text will label options "Option {n1}:", '
        f'"Option {n2}:", "Option {n3}:" in order. End JSON with selection_hint like '
        f'"Choose one option number ({wj.selection_hint_triple(opt_s)}), send your own backstory, '
        'or ask for different proposals."'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    default_hint = (
        f"Choose one option number ({wj.selection_hint_triple(opt_s)}), send your own "
        "backstory, or ask for different proposals."
    )
    last_error: Exception | None = None
    last_raw = ""

    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)

            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=7000,
            )

            json_data = wj.parse_wizard_json_object(last_raw)
            options = json_data.get("options") if isinstance(json_data, dict) else None
            if isinstance(options, list) and len(options) == 3:
                formatted = []
                for idx, option in enumerate(options):
                    formatted.append(
                        f"Option {opt_s + idx}: {option.get('title', '')}\n"
                        f"{option.get('paragraph_1', '')}\n\n"
                        f"{option.get('paragraph_2', '')}"
                    )
                formatted.append(default_hint)
                return {
                    "text": "\n\n".join(formatted).strip(),
                    "raw": last_raw,
                    "valid": True,
                    "options": options,
                    "optionStartUsed": opt_s,
                }

            extracted = wj.extract_answer_text(last_raw)
            if extracted and wj.prose_has_three_options_at(extracted, opt_s):
                needs_hint = not bool(
                    re.search(r"choose one|send your own|different proposal", extracted, re.I)
                )
                text_out = f"{extracted.strip()}\n\n{default_hint}" if needs_hint else extracted.strip()
                return {
                    "text": text_out,
                    "raw": last_raw,
                    "valid": True,
                    "options": None,
                    "optionStartUsed": opt_s,
                }

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response format was incorrect. Retry with valid JSON only. "
                            "Provide exactly 3 objects in \"options\". Do NOT paste or summarize "
                            "the world context in your reply—only the three character backstory "
                            "options and selection_hint."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    fallback = wj.extract_answer_text(last_raw) or last_raw
    valid = wj.prose_has_three_options_at(fallback, opt_s)
    text_out = (
        f"{str(fallback).strip()}\n\n{default_hint}"
        if valid and not re.search(r"choose one|send your own|different proposal", str(fallback), re.I)
        else str(fallback).strip()
    )
    return {
        "text": text_out,
        "raw": last_raw,
        "valid": valid,
        "error": str(last_error) if last_error else None,
        "optionStartUsed": opt_s,
    }


async def generateInventoryOptions(
    theme: str,
    player_name: str,
    world_information: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    option_start: int = 1,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    opt_s = wj.clamp_option_start(option_start)
    messages = [
        {
            "role": "system",
            "content": (
                "Generate exactly 3 inventory option sets as valid JSON only. "
                "Return this shape only: "
                '{"options":[{"title":"...","items":[{"item_id":"...","name":"...","description":"...","quantity":1,"usage":"...","rarity":"...","weight":0,"tags":["..."]}],"currency":["low","mid","high"]}],"selection_hint":"..."}'
                f"\n\n{WIZARD_JSON_OUTPUT_RULES}\n\n{INVENTORY_EXCLUDE_WEAPON_ARMOR}\n\n"
                f"{STARTER_INVENTORY_GUIDANCE}\n\n{BACKSTORY_ANCHOR_GUIDANCE}\n\n{CURRENCY_GUIDANCE}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Genre tag: {theme}\n"
                f"Player: {player_name}\n"
                f"{wj.build_character_context_block(rules_context)}\n"
                f"Cumulative wizard context (world + backstory + prior steps):\n{world_information}\n"
                f"{wj.build_backstory_anchor_block(rules_context)}\n"
                f"{wj.build_rules_context_block(rules_context)}\n"
                "Provide 3 starter inventory options with structured items (item_id internal merge "
                "key only; also name,description,quantity,usage) and currency[3] tier names. "
                "Anchor at least one option to "
                "items explicitly mentioned in the backstory when present. Do not expose item_id in "
                "option titles or bullet text shown to the player. None of the three options "
                "may center on a primary weapon or worn armor. The formatted output will label "
                f"them Option {opt_s}:, Option {opt_s + 1}:, Option {opt_s + 2}:. End with hint "
                f"that player can choose one ({wj.selection_hint_triple(opt_s)}), provide own "
                "inventory, or ask for new options."
            ),
        },
    ]

    last_raw = ""
    last_error: Exception | None = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=4096,
            )
            json_data = wj.parse_wizard_json_object(last_raw)
            options = json_data.get("options") if isinstance(json_data, dict) else None
            if isinstance(options, list) and len(options) >= 3:
                return {
                    "text": wj.format_inventory_options(options, opt_s),
                    "raw": last_raw,
                    "valid": True,
                    "optionStartUsed": opt_s,
                }
            if isinstance(options, list) and len(options) >= 1 and attempt >= DEFAULT_RETRIES:
                return {
                    "text": wj.format_inventory_options(options, opt_s),
                    "raw": last_raw,
                    "valid": False,
                    "error": f"Expected 3 inventory options, got {len(options)}.",
                    "optionStartUsed": opt_s,
                }

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response format was incorrect or got cut off. Retry with valid, "
                            "COMPLETE JSON only (no prose, no markdown fences): exactly 3 options, "
                            "each with items, currency[3], and a short items list."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    formatted = wj.try_format_inventory_options_json(last_raw, opt_s)
    if not formatted:
        extracted = wj.extract_answer_text(last_raw) or last_raw
        formatted = wj.try_format_inventory_options_json(str(extracted), opt_s)
    if formatted:
        return {
            "text": formatted,
            "raw": last_raw,
            "valid": False,
            "error": (str(last_error).strip() if last_error else "") or "Incomplete inventory options batch.",
            "optionStartUsed": opt_s,
        }

    extracted = wj.extract_answer_text(last_raw) or last_raw
    return {
        "text": extracted,
        "raw": last_raw,
        "valid": False,
        "error": (str(last_error).strip() if last_error else "") or None,
        "optionStartUsed": opt_s,
    }


async def generateInventorySummary(
    theme: str,
    player_name: str,
    world_information: str,
    current_draft: str,
    player_request: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    dialog_transcript: str | list[dict[str, str]] | None = "",
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    system_prompt = (
        "Refine inventory as valid JSON only. OPERATION MODE: PATCH / MERGE (not rewrite).\n\n"
        "STRICT PATCH RULES:\n"
        '- Treat "Current inventory JSON" as authoritative.\n'
        "- Copy every unchanged item verbatim into your output — same item_id, name, "
        "description, quantity, usage, rarity, weight, tags.\n"
        "- Only ADD new items or MODIFY specific items that the player explicitly asked to change.\n"
        "- Only REMOVE items if the player explicitly asks to remove them.\n"
        "- Never drop, rename, or merge pre-existing items silently.\n"
        "- Keep item_id stable for any item that already existed.\n"
        "- If the Request explicitly contradicts an existing item (wrong quantity, remove, "
        "replace, retcon), the PLAYER WINS: apply that change and make the rest coherent — "
        "do not keep two conflicting versions of the same item.\n\n"
        "FORBIDDEN: Never output curly-brace placeholders such as {name}, {item_id}, "
        "{description}, {usage}, or {rarity}. Every string must be final in-world text. "
        "For new items use concrete item_id slugs (e.g. med_patch_01). When adding an item, "
        "fill all required fields with real words.\n\n"
        f"{INVENTORY_EXCLUDE_WEAPON_ARMOR}\n"
        f"{STARTER_INVENTORY_GUIDANCE}\n"
        f"{BACKSTORY_ANCHOR_GUIDANCE}\n"
        f"{CURRENCY_GUIDANCE}\n"
        "If the player asks for a weapon or armor, do not add it to this JSON; those belong "
        "in the Gear step. You may omit or rephrase—still return valid items."
    )

    draft_text = wj.strip_ui_markers(str(current_draft or "").strip())
    world_full = wj.strip_ui_markers(str(world_information or "").strip())
    world_max = 9000 if draft_text else 16000
    world_ctx = world_full[:world_max] if world_full else ""
    dialog_text = _dialog_text(dialog_transcript)

    user_prompt = wj.wizard_context_authority_preamble()
    user_prompt += f"Genre tag: {theme}\nPlayer: {player_name}\n"
    user_prompt += f"{wj.build_character_context_block(rules_context)}\n"
    user_prompt += wj.build_backstory_anchor_block(rules_context)
    if world_ctx:
        user_prompt += (
            "Cumulative wizard context (BACKGROUND ONLY — world + backstory + prior steps; for tone and "
            "consistency; Request + Current inventory JSON win on conflicts):\n"
            f"{world_ctx}\n\n"
        )
    if dialog_text:
        user_prompt += (
            "Wizard inventory chat (continuity — earlier turns; not the canonical inventory by itself):\n"
            f"{dialog_text[:12000]}\n\n"
        )
    user_prompt += (
        f"Current inventory JSON:\n{current_draft}\n"
        f"Request: {player_request}\n"
        f"{wj.build_rules_context_block(rules_context)}\n"
        "Apply minimal changes. item_id is an internal stable key for merging (never shown to "
        "the player); keep it unchanged for untouched items. Return the COMPLETE inventory as "
        '{items:[...], currency:[3 tier names]} — every pre-existing item must still be present '
        "(unless the player explicitly asked to drop it). Fields per item: item_id, name, "
        "description, quantity, usage and optional rarity, weight, tags.\n"
        "currency must be exactly three short tier names (low/mid/high). Keep currency unchanged "
        "unless the player explicitly asks to rename or replace the currency tiers.\n\n"
        "All string fields must be literal text, not templates.\n\n"
        "Do not add primary weapons or worn armor to this list; redirect that intent to the Gear "
        "tab conceptually (output JSON only, no prose)."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_raw = ""
    last_error: Exception | None = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=4096,
            )
            json_data = wj.parse_wizard_json_object(last_raw)
            items = json_data.get("items") if isinstance(json_data, dict) else None
            currency = json_data.get("currency") if isinstance(json_data, dict) else None
            if isinstance(items, list):
                if wj.inventory_items_look_like_placeholders(items):
                    if attempt < DEFAULT_RETRIES:
                        messages.append({"role": "assistant", "content": last_raw})
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Your reply used placeholder tokens like {name} or {item_id} "
                                    "instead of real text. Output the COMPLETE items array again: "
                                    "copy every unchanged item exactly from Current inventory JSON, "
                                    "and for any new or edited item use real name, description, "
                                    "item_id, usage (no curly-brace templates)."
                                ),
                            }
                        )
                        continue
                    return {"text": last_raw, "raw": last_raw, "valid": False}
                if not isinstance(currency, list) or len(currency) != 3:
                    if attempt < DEFAULT_RETRIES:
                        messages.append({"role": "assistant", "content": last_raw})
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    'currency must be an array of exactly 3 tier names. Return '
                                    '{items:[...], currency:[low, mid, high]} with real strings.'
                                ),
                            }
                        )
                        continue
                    return {"text": last_raw, "raw": last_raw, "valid": False}
                return {"text": _json_text(json_data), "raw": last_raw, "valid": True}

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            'Return valid JSON only: a single object with an "items" array and '
                            '"currency" array of exactly 3 tier names. '
                            "Each item needs item_id, name, description, quantity (integer), "
                            "usage — all real strings, never {placeholders}."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    formatted_options = wj.try_format_inventory_options_json(last_raw, 1)
    if formatted_options:
        return {
            "text": formatted_options,
            "raw": last_raw,
            "valid": False,
            "error": str(last_error) if last_error else "Reply was inventory options, not items JSON.",
        }

    return {
        "text": wj.extract_answer_text(last_raw) or last_raw,
        "raw": last_raw,
        "valid": False,
        "error": str(last_error) if last_error else None,
    }


async def generateGearOptions(
    theme: str,
    player_name: str,
    world_information: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    option_start: int = 1,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    opt_s = wj.clamp_option_start(option_start)
    messages = [
        {
            "role": "system",
            "content": (
                "Generate exactly 3 gear options as valid JSON only. Return this shape only: "
                '{"options":[{"title":"...","weapon":{"name":"...","description":"...","attack_bonus":0,"damage":"1d8","weapon_type":"...","special_effects":[]},"armor":{"name":"...","description":"...","ac":12,"armor_type":"...","special_effects":[]}}],"selection_hint":"..."}'
                f"\n{WIZARD_JSON_OUTPUT_RULES}\n\n{STARTER_GEAR_GUIDANCE}\n\n{BACKSTORY_GEAR_ANCHOR_GUIDANCE}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Genre tag: {theme}\nPlayer: {player_name}\n"
                f"{wj.build_character_context_block(rules_context)}\n"
                f"Cumulative wizard context (world + backstory + prior steps):\n{world_information}\n"
                f"{wj.build_backstory_anchor_block(rules_context)}\n"
                f"{wj.build_rules_context_block(rules_context)}\n"
                "Provide 3 starter gear loadouts with weapon and armor structures. At least one option "
                "must reflect weapon or armor explicitly named in the backstory when present. Include special_effects arrays "
                "(usually empty). weapon.damage must be dice notation (1d8, 1d6+2), never flat numbers. "
                f"Formatted output labels: Option {opt_s}:, Option {opt_s + 1}:, Option {opt_s + 2}:. "
                f"End with hint that user can choose one ({wj.selection_hint_triple(opt_s)}), provide "
                "own setup, or ask for new options."
            ),
        },
    ]

    last_raw = ""
    last_error: Exception | None = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=4096,
            )
            json_data = wj.parse_wizard_json_object(last_raw)
            options = json_data.get("options") if isinstance(json_data, dict) else None
            if not isinstance(options, list) or not options:
                options = wj.salvage_gear_options(last_raw)
            if isinstance(options, list) and len(options) >= 3:
                normalized_options = []
                for option in options[:3]:
                    opt = dict(option)
                    gear = wj.normalize_gear_json(
                        {
                            "weapon": dict(opt.get("weapon") or {}),
                            "armor": dict(opt.get("armor") or {}),
                        }
                    )
                    opt["weapon"] = gear.get("weapon") or opt.get("weapon") or {}
                    opt["armor"] = gear.get("armor") or opt.get("armor") or {}
                    normalized_options.append(opt)
                return {
                    "text": wj.format_gear_options(normalized_options, opt_s),
                    "raw": last_raw,
                    "valid": True,
                    "optionStartUsed": opt_s,
                }
            if isinstance(options, list) and len(options) >= 1 and attempt >= DEFAULT_RETRIES:
                normalized_options = []
                for option in options[:3]:
                    opt = dict(option)
                    gear = wj.normalize_gear_json(
                        {
                            "weapon": dict(opt.get("weapon") or {}),
                            "armor": dict(opt.get("armor") or {}),
                        }
                    )
                    opt["weapon"] = gear.get("weapon") or opt.get("weapon") or {}
                    opt["armor"] = gear.get("armor") or opt.get("armor") or {}
                    normalized_options.append(opt)
                return {
                    "text": wj.format_gear_options(normalized_options, opt_s),
                    "raw": last_raw,
                    "valid": False,
                    "error": f"Expected 3 gear options, got {len(options)}.",
                    "optionStartUsed": opt_s,
                }

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response format was incorrect or got cut off. Retry with valid, "
                            "COMPLETE JSON only (no prose, no markdown fences): exactly 3 starter "
                            "gear options, each with weapon and armor. weapon.damage MUST be dice "
                            "notation (1d4, 1d6, 1d8, 1d10, 2d6, 1d6+2) — never flat numbers."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    formatted = wj.try_format_gear_options_json(last_raw, opt_s)
    if not formatted:
        extracted = wj.extract_answer_text(last_raw) or last_raw
        formatted = wj.try_format_gear_options_json(str(extracted), opt_s)
    if formatted:
        return {
            "text": formatted,
            "raw": last_raw,
            "valid": False,
            "error": (str(last_error).strip() if last_error else "") or "Incomplete gear options batch.",
            "optionStartUsed": opt_s,
        }

    extracted = wj.extract_answer_text(last_raw) or last_raw
    return {
        "text": extracted,
        "raw": last_raw,
        "valid": False,
        "error": (str(last_error).strip() if last_error else None),
        "optionStartUsed": opt_s,
    }


async def generateGearSummary(
    theme: str,
    player_name: str,
    world_information: str,
    current_draft: str,
    player_request: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    dialog_transcript: str | list[dict[str, str]] | None = "",
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    system_prompt = (
        "Refine weapon and armor as valid JSON only. OPERATION MODE: PATCH / MERGE (not rewrite).\n\n"
        "STRICT PATCH RULES:\n"
        '- Treat "Current gear JSON" as authoritative.\n'
        "- Copy the unchanged slot (weapon OR armor) verbatim — same name, damage/defense, "
        "special_effects, description.\n"
        "- Only MODIFY the slot the player explicitly asked to change.\n"
        "- Never swap both weapon and armor at once unless the player explicitly asks to replace both.\n"
        "- Never rename an existing weapon or armor that the player did not ask to change.\n"
        "- If the Request explicitly contradicts the current gear (replace both slots, full new "
        "loadout), the PLAYER WINS: output coherent gear that matches the Request.\n\n"
        f"{STARTER_GEAR_GUIDANCE}\n"
        f"{BACKSTORY_GEAR_ANCHOR_GUIDANCE}"
    )
    draft_text = wj.strip_ui_markers(str(current_draft or "").strip())
    world_full = wj.strip_ui_markers(str(world_information or "").strip())
    world_max = 9000 if draft_text else 16000
    world_ctx = world_full[:world_max] if world_full else ""
    dialog_text = _dialog_text(dialog_transcript)

    user_prompt = wj.wizard_context_authority_preamble()
    user_prompt += f"Genre tag: {theme}\nPlayer: {player_name}\n"
    user_prompt += f"{wj.build_character_context_block(rules_context)}\n"
    user_prompt += wj.build_backstory_anchor_block(rules_context)
    if world_ctx:
        user_prompt += (
            "Cumulative wizard context (BACKGROUND ONLY — world + backstory + prior steps; Request + Current gear JSON win on conflicts):\n"
            f"{world_ctx}\n\n"
        )
    if dialog_text:
        user_prompt += (
            "Wizard gear chat (continuity — earlier turns; not the canonical gear by itself):\n"
            f"{dialog_text[:12000]}\n\n"
        )
    user_prompt += (
        f"Current gear JSON:\n{current_draft}\n"
        f"Request: {player_request}\n"
        f"{wj.build_rules_context_block(rules_context)}\n"
        "Apply minimal changes and keep stable fields untouched. Return {weapon:{...}, armor:{...}} "
        "with required fields — the slot the player did NOT ask to change must be copied verbatim "
        "from Current gear JSON."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_raw = ""
    last_error: Exception | None = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=4096,
            )
            json_data = wj.parse_wizard_json_object(last_raw)
            if (
                isinstance(json_data, dict)
                and isinstance(json_data.get("weapon"), dict)
                and isinstance(json_data.get("armor"), dict)
            ):
                normalized = wj.normalize_gear_json(json_data)
                return {"text": _json_text(normalized), "raw": last_raw, "valid": True}

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            'Return valid JSON only: a single object with "weapon" and "armor" '
                            "fields populated with real data (name, stats, description). "
                            "weapon.damage must be dice notation (1d8, 1d6+2), never flat numbers."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    return {
        "text": wj.extract_answer_text(last_raw) or last_raw,
        "raw": last_raw,
        "valid": False,
        "error": str(last_error) if last_error else None,
    }


async def generateOpeningOptions(
    theme: str,
    player_name: str,
    world_information: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    option_start: int = 1,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    opt_s = wj.clamp_option_start(option_start)
    messages = [
        {
            "role": "system",
            "content": (
                "Generate exactly 3 opening+time options as valid JSON only. "
                'Return this shape only: {"options":[{"title":"...","opening_text":"...","time_hint":"..."}],"selection_hint":"..."}\n\n'
                f"{WIZARD_JSON_OUTPUT_RULES}\n\n"
                "Each option must include a strong, playable opening_text written as 3–5 paragraphs "
                "(separated by blank lines). It must be vivid and specific (locations, sensory details, "
                "immediate tension), grounded in the provided world. Do not be generic or one-sentence.\n\n"
                "time_hint MUST be a 2-line markdown table with EXACT header columns:\n"
                "| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | Current Location | Season | Weather |\n"
                "| (data row matching the header columns) |"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Genre tag: {theme}\nPlayer: {player_name}\n"
                f"Cumulative wizard context (world + prior steps):\n{world_information}\n"
                f"{wj.build_rules_context_block(rules_context)}\n\n"
                "Provide 3 options with fields: title, opening_text, time_hint.\n"
                "opening_text requirements:\n"
                "- 3–5 paragraphs\n"
                "- starts in-media-res with a concrete situation the player can act on immediately\n"
                "- include 2–3 actionable details or NPC/scene hooks inside the text (not a separate list)\n"
                "time_hint requirements:\n"
                "- Must be the EXACT 2-line table format specified above (header + one data row)\n"
                "- Use an in-world era/year/month/day format in the third column\n\n"
                f"End with selection_hint that the player can choose one option ({wj.selection_hint_triple(opt_s)}), send their own opening, or ask for different proposals."
            ),
        },
    ]

    last_raw = ""
    last_error: Exception | None = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=4096,
            )
            json_data = wj.parse_wizard_json_object(last_raw)
            options = json_data.get("options") if isinstance(json_data, dict) else None
            if isinstance(options, list) and len(options) == 3:
                formatted: list[str] = []
                for idx, option in enumerate(options):
                    opening = str(option.get("opening_text", "") or "").strip()
                    time_hint = str(option.get("time_hint", "") or "").strip()
                    formatted.append(f"Option {opt_s + idx}: {option.get('title', '')}")
                    if opening:
                        formatted.append("")
                        formatted.append(opening)
                        formatted.append("")
                    formatted.append(f"Time: {time_hint}")
                    formatted.append("")
                formatted.append(
                    f"Choose one option ({wj.selection_hint_triple(opt_s)}), send your own opening, or ask for new options."
                )
                return {"text": "\n".join(formatted).strip(), "raw": last_raw, "valid": True, "optionStartUsed": opt_s}

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response format was incorrect or got cut off. Retry with valid, "
                            "COMPLETE JSON only (no prose, no markdown fences): exactly 3 options "
                            "with title, opening_text, time_hint."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    return {
        "text": wj.extract_answer_text(last_raw) or last_raw,
        "raw": last_raw,
        "valid": False,
        "error": (str(last_error).strip() if last_error else None),
        "optionStartUsed": opt_s,
    }


async def generateOpeningSummary(
    theme: str,
    player_name: str,
    world_information: str,
    current_draft: str,
    player_request: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    dialog_transcript: str | list[dict[str, str]] | None = "",
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    clean_draft = wj.strip_ui_markers(str(current_draft or "").strip())
    draft_text = clean_draft
    world_full = wj.strip_ui_markers(str(world_information or "").strip())
    world_max = 9000 if draft_text else 16000
    world_ctx = world_full[:world_max] if world_full else ""
    dialog_text = _dialog_text(dialog_transcript)

    user_prompt = wj.wizard_context_authority_preamble()
    user_prompt += f"Genre tag: {theme}\nPlayer: {player_name}\n"
    if world_ctx:
        user_prompt += (
            "Cumulative wizard context (BACKGROUND ONLY — world + prior steps; Request + Current opening JSON win on conflicts):\n"
            f"{world_ctx}\n\n"
        )
    if dialog_text:
        user_prompt += (
            "Wizard opening chat (continuity — earlier turns; not the canonical opening by itself):\n"
            f"{dialog_text[:12000]}\n\n"
        )
    user_prompt += (
        f"Current opening JSON:\n{current_draft}\n"
        f"Request: {player_request}\n{wj.build_rules_context_block(rules_context)}\n\n"
        "Instructions:\n"
        '- If the player only picks an option by number (e.g. "Option 3") with no other edits, return the Current opening JSON verbatim: same opening_text and time_hint (full table); do not substitute generic times.\n'
        "- Keep time_hint unchanged unless the player explicitly asks to change it.\n"
        "- If the Request contradicts the Current opening JSON (different time or premise), the PLAYER WINS: update opening_text and/or time_hint to match the Request coherently — do not keep contradictory versions.\n"
        '- If the player asks for "more detail" / "expand", expand opening_text by adding concrete sensory detail, NPC intent, and immediate stakes, without changing the premise unless the Request asks to.\n'
        "- If you change time_hint, it must remain in the EXACT 2-line table format required above.\n"
        "- Return {opening_text,time_hint}."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "Refine opening+time as valid JSON only.\n"
                "PATCH mode: treat Current opening JSON as authoritative for structure and continuity.\n"
                "Apply the changes explicitly requested by the player; when the Request contradicts the Current JSON, resolve in favour of the Request and keep one coherent version.\n"
                "Do NOT rewrite unrelated paragraphs of opening_text when there is no contradiction.\n"
                "opening_text must be vivid and playable, written as 3–6 paragraphs (separated by blank lines).\n"
                "time_hint MUST be a 2-line markdown table with EXACT header columns:\n"
                "| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | Current Location | Season | Weather |\n"
                "| (data row matching the header columns) |"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    last_raw = ""
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=7000,
            )

            json_data = wj.parse_wizard_json_object(last_raw)
            if isinstance(json_data, dict):
                opening_text = str(json_data.get("opening_text", "") or "").strip()
                time_hint = str(json_data.get("time_hint", "") or "").strip()
                if opening_text and time_hint:
                    if len(opening_text) < 300:
                        if attempt < DEFAULT_RETRIES:
                            messages.append({"role": "assistant", "content": last_raw})
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "opening_text is too short. Return valid JSON with opening_text "
                                        "as 3–6 full paragraphs (>= 300 characters), plus time_hint."
                                    ),
                                }
                            )
                            continue
                        return {
                            "text": wj.extract_answer_text(last_raw) or last_raw,
                            "raw": last_raw,
                            "valid": False,
                        }

                    return {"text": _json_text(json_data), "raw": last_raw, "valid": True}

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response format was incorrect. Retry: valid JSON with opening_text "
                            "(string), time_hint (2-line table)."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    json_fallback = wj.parse_wizard_json_object(last_raw)
    if isinstance(json_fallback, dict):
        opening_text = str(json_fallback.get("opening_text", "") or "").strip()
        time_hint = str(json_fallback.get("time_hint", "") or "").strip()
        if opening_text and time_hint and len(opening_text) >= 300:
            return {"text": _json_text(json_fallback), "raw": last_raw, "valid": True}

    return {
        "text": wj.extract_answer_text(last_raw) or last_raw,
        "raw": last_raw,
        "valid": False,
        "error": str(last_error) if last_error else None,
    }


async def generateWorldSummary(
    theme: str,
    campaign_length: str,
    current_draft: str,
    player_request: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    dialog_transcript: str | list[dict[str, str]] | None = "",
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    system_prompt = """You are an RPG world creation assistant. Refine or create the world definition based on player input.

Output format MUST be valid JSON with a "world_definition" field containing the complete world text.

The world_definition value must be plain prose for the player: paragraphs and optional bullet lists (markdown-friendly) describing the setting, tone, conflicts, and factions. Do NOT embed a second JSON document, schema dump, stat blocks, or key-value machine format inside world_definition. Never output the tokens [lb] or [rb] — use normal square brackets [ ] only when you mean literal brackets in readable text.

Mark thinking with: (Thinking start) ... (Thinking stop)
Mark final answer with: (Answer start) ... (Answer stop)

Inside Answer markers, the content must be valid JSON.

IMPORTANT SCOPE:
- Keep output strictly to WORLD information.
- Do NOT introduce character class/subclass/build/stats/race suggestions.
- Do NOT define starting location/opening encounter details.
- If the player asks for character build or start location, acknowledge briefly and keep world definition unchanged except for world-level context.
- Work incrementally: preserve existing approved world details and apply only requested changes.
- Do NOT replace the whole world with a brand-new storyline unless the user explicitly asks for a full reset.
- Respect explicit removals from the player (if player removes an element, do not reintroduce it unless asked).

PATCH (when refining existing world text in JSON):
- Default: keep sentences that still match the player's request unchanged where practical.
- If the Request contradicts concrete facts in the current world text, the PLAYER WINS: remove or rewrite conflicting passages — do not leave both versions.
- When there is no contradiction, avoid paraphrasing unrelated paragraphs."""

    clean_draft = (
        wj.strip_ui_markers(str(current_draft or "").strip())
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    selected_campaign = wj.pick_selected_campaign(clean_draft, player_request)
    dialog_text = _dialog_text(dialog_transcript)

    user_prompt = (
        wj.wizard_context_authority_preamble()
        + f"Genre tag: {theme}\nCampaign length: {campaign_length}\n{wj.build_rules_context_block(rules_context)}\n\n"
    )
    if dialog_text:
        user_prompt += (
            "Wizard world-definition chat (continuity — earlier turns; not the canonical world definition by itself):\n"
            f"{dialog_text[:12000]}\n\n"
        )

    if selected_campaign:
        user_prompt += (
            "Selected campaign from current proposals:\n"
            f"Campaign {selected_campaign['number']}: {selected_campaign['title']}\n"
            f"{selected_campaign['body']}\n\n"
            f"Player request: {player_request or 'Please refine this world definition.'}\n\n"
            "IMPORTANT:\n"
            "- Build ONE cohesive world definition from the selected campaign only.\n"
            "- Do NOT return list format with Campaign 1/2/3.\n"
            '- Do NOT include labels like "(SELECTED)" in final text.\n'
            "- Return a normal multi-paragraph world definition as JSON.\n"
            "- Keep it as a reusable WORLD DEFINITION, not as a full plot synopsis for one hero.\n"
            "- Focus on setting pillars, factions, tone, conflicts, and world rules."
        )
    elif clean_draft:
        user_prompt += f"Current world definition:\n{clean_draft}\n\n"
        user_prompt += f"Player request: {player_request or 'Please refine this world definition.'}\n\n"
        user_prompt += (
            "Update the world definition based on the player's request.\n"
            "OPERATION MODE: PATCH (not rewrite)\n"
            "Apply minimal-diff editing:\n"
            "- Keep all existing sections unless user explicitly asks to remove/replace them.\n"
            "- Modify only the parts related to the request.\n"
            "- Preserve naming and terminology already accepted by the player.\n"
            "- Preserve the existing setting, factions, lore anchors, and tone unless user explicitly asks to change them.\n"
            "- Add or adjust details instead of replacing the whole definition.\n"
            "- When the Request contradicts the current world text, resolve in favour of the Request (rewrite or remove conflicting facts; do not keep contradictions).\n"
            "- Never include player class/subclass/build lists or playable role options.\n"
            "- Do NOT repeat sections/headings; each concept should appear once.\n"
            "Return the complete updated definition as JSON."
        )
    else:
        user_prompt += f"Player request: {player_request or 'Create a detailed world definition.'}\n\n"
        user_prompt += "Create a comprehensive world definition. Return it as JSON."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    last_raw = ""
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=7000,
            )

            json_data = wj.parse_wizard_json_object(last_raw)
            world_definition = json_data.get("world_definition") if isinstance(json_data, dict) else None
            if isinstance(world_definition, str) and world_definition.strip():
                inner = wj.repair_wizard_definition_text(world_definition.strip())
                sanitized = wj.dedupe_repeated_long_lines(
                    wj.dedupe_paragraph_blocks(wj.sanitize_world_definition(inner))
                )
                return {"text": sanitized, "raw": last_raw, "valid": True}

            extracted = wj.extract_answer_text(last_raw)
            if extracted and len(extracted) > 50:
                sanitized = wj.dedupe_repeated_long_lines(
                    wj.dedupe_paragraph_blocks(
                        wj.sanitize_world_definition(wj.repair_wizard_definition_text(extracted))
                    )
                )
                return {"text": sanitized, "raw": last_raw, "valid": True}

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            'Your response format was incorrect. Retry: valid JSON with a single "world_definition" string. '
                            "That string must be plain prose for the player (no nested JSON / schema dump inside it). "
                            "Never output the tokens [lb] or [rb]; use normal square brackets only if you need literal brackets in text."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    fallback = wj.extract_answer_text(last_raw) or last_raw
    sanitized_fallback = wj.dedupe_repeated_long_lines(
        wj.dedupe_paragraph_blocks(
            wj.sanitize_world_definition(wj.repair_wizard_definition_text(fallback))
        )
    )
    return {
        "text": sanitized_fallback,
        "raw": last_raw,
        "valid": len(sanitized_fallback) > 50,
        "error": str(last_error) if last_error else None,
    }


async def generateBackstorySummary(
    theme: str,
    player_name: str,
    current_draft: str,
    player_request: str,
    llm_config: dict[str, Any] | None,
    rules_context: dict[str, Any] | None = None,
    world_information: str = "",
    character_profile: str = "",
    dialog_transcript: str | list[dict[str, str]] | None = "",
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    system_prompt = """You are an RPG character creation assistant. PATCH-edit a character backstory.

Output format MUST be valid JSON with a "backstory_definition" field containing the complete backstory text.

If a world reference is included, use it only for consistency. Do NOT paste or rewrite the full world bible into backstory_definition—only the character's story.
Respect the world's era, technology, geography, and whether magic or supernatural elements exist — do not introduce fantasy tropes that contradict a mundane or explicitly non-magical world.

PATCH MODE (when Current backstory is provided):
- Default: preserve unchanged sentences verbatim; apply the edits the player asked for.
- If the player's request CONTRADICTS concrete facts in Current backstory, the PLAYER WINS: remove or rewrite every conflicting sentence — do not leave both versions in the text.
- Apply ONLY what the player asked for when it is additive or clarifying; when it is a contradiction, rewrite as much as needed to make the story coherent.
- Do NOT reset the whole backstory unless the player explicitly asks for a full restart or a full regeneration from their stated themes.
- If the request is ambiguous and does not contradict the draft, keep the original wording for unclear parts.
- Facts the player has clearly established in the Request outrank older prose in Current backstory and outrank generic flourishes from setting context or old option batches.

Mark thinking with: (Thinking start) ... (Thinking stop)
Mark final answer with: (Answer start) ... (Answer stop)

Inside Answer markers, the content must be valid JSON."""

    profile_text = wj.strip_ui_markers(str(character_profile or "").strip())
    profile_block = (
        "Fixed character sheet facts (the backstory MUST stay consistent with these; do not "
        f"contradict race, class, gender, or age):\n{profile_text}\n\n"
        if profile_text
        else ""
    )

    user_prompt = (
        wj.wizard_context_authority_preamble()
        + f"Genre tag: {theme}\nCharacter name: {player_name}\n{profile_block}{wj.build_rules_context_block(rules_context)}\n"
    )

    draft_text = wj.strip_ui_markers(str(current_draft or "").strip())
    world_full = wj.strip_ui_markers(str(world_information or "").strip())
    world_max = 9000 if draft_text else 16000
    world_ctx = world_full[:world_max] if world_full else ""
    if world_ctx:
        user_prompt += (
            "Cumulative setting context — campaign, world definition, rules, and ability snapshot "
            "(BACKGROUND ONLY — may be very long; do NOT paste into backstory_definition; use for tone "
            "and consistency only; do not override explicit facts in Current backstory or the player's "
            f"Request):\n{world_ctx}\n\n"
        )
        user_prompt += (
            "The backstory must obey this world: same rough time period and place, same rules about "
            "magic/supernatural (if the world is mundane, stay mundane). Do not add contradictions.\n\n"
        )

    dialog_text = _dialog_text(dialog_transcript)
    if dialog_text:
        user_prompt += (
            "Wizard backstory chat (continuity — earlier player requests and assistant replies including "
            f"option batches; not the canonical definition by itself):\n{dialog_text[:12000]}\n\n"
        )

    if draft_text:
        user_prompt += (
            "Current backstory (baseline text to PATCH — keep sentences that still match the player's request):\n"
            f"{draft_text}\n\n"
            f"Player request: {player_request or 'Please refine this backstory.'}\n\n"
            "Operation mode: PATCH. Apply the player's request to the backstory.\n"
            "When the request conflicts with Current backstory, resolve the conflict in favour of the request "
            "(rewrite or remove conflicting facts; do not keep contradictions).\n"
            "When there is no conflict, keep unchanged sentences verbatim (do not paraphrase unrelated paragraphs).\n"
            'Return the complete updated backstory as JSON in "backstory_definition".'
        )
    else:
        user_prompt += f"Player request: {player_request or 'Create a detailed character backstory.'}\n\n"
        user_prompt += "Create an immersive backstory. Use clear paragraphs with specific details. Return it as JSON."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    last_raw = ""
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=7000,
            )

            json_data = wj.parse_wizard_json_object(last_raw)
            backstory_definition = (
                json_data.get("backstory_definition") if isinstance(json_data, dict) else None
            )
            if isinstance(backstory_definition, str) and backstory_definition.strip():
                return {
                    "text": wj.repair_wizard_definition_text(backstory_definition.strip()),
                    "raw": last_raw,
                    "valid": True,
                }

            extracted = wj.extract_answer_text(last_raw)
            if extracted and len(extracted) > 50:
                return {
                    "text": wj.repair_wizard_definition_text(str(extracted).strip()),
                    "raw": last_raw,
                    "valid": True,
                }

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            'Your response format was incorrect. Please retry with valid JSON containing a "backstory_definition" field.'
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    fallback = wj.repair_wizard_definition_text(wj.extract_answer_text(last_raw) or last_raw)
    return {
        "text": fallback,
        "raw": last_raw,
        "valid": len(fallback) > 50,
        "error": str(last_error) if last_error else None,
    }


async def generatePortraitSdPrompts(
    theme_label: str,
    player_name: str,
    backstory: str,
    world_information: str,
    style_override: str,
    llm_config: dict[str, Any] | None,
    character_profile: str = "",
    appearance_visual: str = "",
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    backstory_text = str(backstory or "").strip()[:12000]
    world_text = str(world_information or "").strip()[:8000]
    style_text = str(style_override or "").strip()
    profile_text = str(character_profile or "").strip()[:2000]
    appearance_text = str(appearance_visual or "").strip()[:4000]

    messages = [
        {
            "role": "system",
            "content": (
                "You write prompts for Stable Diffusion character portraits.\n"
                "Output strict JSON only with this shape: "
                '{"subject_positive":"...","negative_extra":"..."}\n'
                "subject_positive: comma-separated VISUAL tags for this character only (species, apparent age, skin, hair, eyes, marks, typical clothing or armor, small props, color palette, mood). Base it on the backstory; do not contradict it.\n"
                "When the player supplied explicit appearance notes, those outrank backstory prose for physical look — still avoid contradicting fixed sheet facts (race, age, gender).\n"
                "Do NOT add generic quality tags (masterpiece, 8k, highly detailed, hdr) — the server appends those.\n"
                "Do NOT describe shot type or crowd — the server locks a waist-up single-character portrait.\n"
                'negative_extra: short comma-separated extra negatives for things to forbid that would mismatch the character (e.g. "beard, eyepatch" if absent). Use empty string if nothing extra.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Genre tag: {theme_label}\n"
                f"Character name: {player_name}\n"
                + (f"Visible character facts (use for species, age appearance, build; do not contradict):\n{profile_text}\n\n" if profile_text else "")
                + (f"{appearance_text}\n\n" if appearance_text else "")
                + (f"World context (trim):\n{world_text}\n\n" if world_text else "")
                + f"Backstory:\n{backstory_text}\n\n"
                + (f"Optional art style hint from app settings: {style_text}\n" if style_text else "")
            ),
        },
    ]

    last_error: Exception | None = None
    last_raw = ""
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            if attempt > 0:
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
            last_raw = await _wizard_call(
                messages,
                llm_config=llm_config,
                owner=owner,
                llm_enabled=llm_enabled,
                default_max_tokens=1024,
            )
            json_data = wj.parse_wizard_json_object(last_raw)
            subject_positive = json_data.get("subject_positive") if isinstance(json_data, dict) else None
            if isinstance(subject_positive, str) and len(subject_positive.strip()) > 3:
                merged = wj.merge_portrait_sd_prompts(
                    theme_label,
                    player_name,
                    style_override,
                    subject_positive,
                    str(json_data.get("negative_extra", "") or ""),
                )
                return {**merged, "raw": last_raw, "valid": True}

            extracted = wj.extract_answer_text(last_raw)
            extracted_json = wj.parse_wizard_json_object(extracted) if extracted else None
            subject_positive = extracted_json.get("subject_positive") if isinstance(extracted_json, dict) else None
            if isinstance(subject_positive, str) and len(subject_positive.strip()) > 3:
                merged = wj.merge_portrait_sd_prompts(
                    theme_label,
                    player_name,
                    style_override,
                    subject_positive,
                    str(extracted_json.get("negative_extra", "") or ""),
                )
                return {**merged, "raw": last_raw, "valid": True}

            if attempt < DEFAULT_RETRIES:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your response format was incorrect. Retry with strict JSON only: "
                            '{"subject_positive":"...","negative_extra":"..."}. '
                            "No markdown fences, no commentary, no thinking blocks — just the JSON object."
                        ),
                    }
                )
        except FugassaLlmDisabled:
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= DEFAULT_RETRIES:
                break

    return {
        "positive_prompt": "",
        "negative_prompt": wj.PORTRAIT_SD_NEGATIVE_BASE,
        "raw": last_raw,
        "valid": False,
        "error": (str(last_error).strip() if last_error else "") or "Invalid portrait prompt JSON from model",
    }


async def generateHomebrewSheet(
    draft: dict[str, Any],
    llm_config: dict[str, Any] | None,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    from titan.fugassa.dnd5e_character_builder import build, draft_to_build_input
    from titan.fugassa.dnd5e_database import get_dnd5e_database
    from titan.fugassa.wizard_homebrew import (
        HOMEBREW_SYSTEM_PROMPT,
        build_homebrew_user_prompt,
        homebrew_sheet_response_format,
        normalize_homebrew_payload,
    )

    build_in = draft_to_build_input(draft)
    preview = build(get_dnd5e_database(), {**build_in, "homebrew_details": {}})
    user_prompt = build_homebrew_user_prompt(draft)
    messages = [
        {"role": "system", "content": HOMEBREW_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    cfg = dict(llm_config or {})
    cfg.setdefault("temperature", min(max(float(cfg.get("temperature", 0.25)), 0.05), 0.28))
    cfg.setdefault("max_tokens", max(int(cfg.get("max_tokens", 2048)), 4096))
    cfg["response_format"] = homebrew_sheet_response_format()
    try:
        raw = await _wizard_call(
            messages,
            llm_config=cfg,
            owner=owner,
            llm_enabled=llm_enabled,
            default_max_tokens=4096,
        )
        parsed = wj.parse_wizard_json_object(raw) or {}
        if not parsed:
            return {"valid": False, "error": "Could not parse homebrew JSON from model", "raw": raw}
        mods = preview.get("ability_modifiers") or {}
        level = int(draft.get("level") or 1)
        normalized = normalize_homebrew_payload(parsed, level=level, mods=mods)
        normalized["_class_label"] = build_in.get("class_label", "")
        normalized["_race_label"] = build_in.get("race_label", "")
        normalized["_subclass_label"] = build_in.get("subclass_label", "")
        normalized["_level"] = level
        return {"valid": True, "homebrew_details": normalized, "raw": raw}
    except FugassaLlmDisabled as exc:
        raise
    except Exception as exc:
        return {"valid": False, "error": str(exc), "raw": ""}


async def generateGameplayResponse(
    messages: list[dict[str, str]],
    llm_config: dict[str, Any] | None,
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    try:
        raw = await _wizard_call(
            messages,
            llm_config=llm_config,
            owner=owner,
            llm_enabled=llm_enabled,
            default_max_tokens=4096,
        )
        extracted = wj.extract_answer_text(raw)
        return {
            "text": extracted,
            "raw": raw,
            "valid": True,
            "usage": None,
            "model": None,
        }
    except Exception as exc:
        return {
            "text": "",
            "raw": "",
            "valid": False,
            "error": str(exc),
        }


# snake_case aliases for Python callers
generate_world_options = generateWorldOptions
generate_backstory_options = generateBackstoryOptions
generate_world_summary = generateWorldSummary
generate_backstory_summary = generateBackstorySummary
generate_inventory_options = generateInventoryOptions
generate_inventory_summary = generateInventorySummary
generate_gear_options = generateGearOptions
generate_gear_summary = generateGearSummary
generate_opening_options = generateOpeningOptions
generate_opening_summary = generateOpeningSummary
generate_portrait_sd_prompts = generatePortraitSdPrompts
generate_homebrew_sheet = generateHomebrewSheet
generate_gameplay_response = generateGameplayResponse

__all__ = [
    "DEFAULT_RETRIES",
    "RETRY_DELAY_MS",
    "generateWorldOptions",
    "generateBackstoryOptions",
    "generateWorldSummary",
    "generateBackstorySummary",
    "generateInventoryOptions",
    "generateInventorySummary",
    "generateGearOptions",
    "generateGearSummary",
    "generateOpeningOptions",
    "generateOpeningSummary",
    "generatePortraitSdPrompts",
    "generateHomebrewSheet",
    "generateGameplayResponse",
    "generate_world_options",
    "generate_backstory_options",
    "generate_world_summary",
    "generate_backstory_summary",
    "generate_inventory_options",
    "generate_inventory_summary",
    "generate_gear_options",
    "generate_gear_summary",
    "generate_opening_options",
    "generate_opening_summary",
    "generate_portrait_sd_prompts",
    "generate_homebrew_sheet",
    "generate_gameplay_response",
]
