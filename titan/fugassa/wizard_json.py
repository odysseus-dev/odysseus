"""JSON / text helpers for the Fugassa wizard engine."""

from __future__ import annotations

import json
import re
from typing import Any

PORTRAIT_SD_SUBJECT_PREFIX = (
    "single character, waist-up portrait, head and shoulders, professional "
    "character art, detailed face, expressive eyes, dramatic lighting, sharp focus"
)
PORTRAIT_SD_SUBJECT_SUFFIX = "masterpiece, best quality, high resolution, intricate details"
PORTRAIT_SD_NEGATIVE_BASE = (
    "lowres, blurry, deformed, bad anatomy, ugly, extra limbs, bad hands, fused fingers, "
    "poorly drawn face, mutation, cropped, text, watermark, logo, signature, multiple "
    "people, crowd, full body, wide shot, nsfw"
)


def repair_corrupted_jsonish(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"\[lb\]", "[", s, flags=re.I)
    s = re.sub(r"\[rb\]", "]", s, flags=re.I)
    s = re.sub(r"\[/lb\]", "]", s, flags=re.I)
    s = re.sub(r"\[/rb\]", "]", s, flags=re.I)
    return s


def repair_wizard_definition_text(text: str) -> str:
    return repair_corrupted_jsonish(text)


def extract_json_object_at(raw: str, start: int = 0) -> dict[str, Any] | None:
    s = repair_corrupted_jsonish(str(raw or "")).strip()
    if start < 0 or start >= len(s):
        return None
    brace = s.find("{", start)
    if brace < 0:
        return None

    depth = 0
    in_string = False
    escape_next = False
    end = -1

    for idx in range(brace, len(s)):
        char = s[idx]
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = idx
                break

    if end < 0:
        return None

    try:
        return json.loads(s[brace : end + 1])
    except json.JSONDecodeError:
        return None


def extract_first_json_object(raw: str) -> dict[str, Any] | None:
    return extract_json_object_at(raw, 0)


def parse_wizard_json_object(raw: str) -> dict[str, Any] | None:
    return extract_first_json_object(repair_corrupted_jsonish(raw))


def extract_answer_text(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return s

    match = re.search(r"\(Answer start\)([\s\S]*?)\(Answer stop\)", s, re.I)
    if match:
        return match.group(1).strip()

    match = re.search(r"<final>([\s\S]*?)</final>", s, re.I)
    if match:
        return match.group(1).strip()

    think_close = s.lower().find("</think>")
    if think_close >= 0:
        return s[think_close + len("</think>") :].strip()

    s = re.sub(r"\(Thinking start\)[\s\S]*?\(Thinking stop\)", "", s, flags=re.I)
    s = re.sub(r"<thinking>[\s\S]*?</thinking>", "", s, flags=re.I)
    s = re.sub(
        r"Thinking Process:[\s\S]*?(?=(Campaign \d|Answer start|<final>|$))",
        "",
        s,
        flags=re.I,
    )
    s = s.replace("</think>", "")
    return s.strip()


def strip_ui_markers(text: str) -> str:
    return re.sub(r"\[UPDATED\]\s*\n?", "", str(text or ""), flags=re.I).strip()


def clamp_option_start(n: int) -> int:
    try:
        value = int(str(n))
    except (TypeError, ValueError):
        return 1
    if value < 1:
        return 1
    return min(value, 997)


def selection_hint_triple(start: int) -> str:
    opt_start = clamp_option_start(start)
    return f"{opt_start}/{opt_start + 1}/{opt_start + 2}"


def sanitize_world_definition(text: str) -> str:
    src = str(text or "").replace("\r", "")
    if not src:
        return ""
    banned = (
        "player character options",
        "player role",
        "starting roles",
        "character class",
        "subclass options",
        "class options",
        "build options",
    )
    out: list[str] = []
    for raw_line in src.split("\n"):
        line = str(raw_line or "")
        lower = line.lower()
        if any(term in lower for term in banned):
            continue
        if re.match(r"^\s*players?\s+may\s+choose\b", line, re.I):
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def dedupe_paragraph_blocks(text: str) -> str:
    src = str(text or "").replace("\r", "").strip()
    if not src:
        return ""
    blocks = re.split(r"\n{2,}", src)
    seen: set[str] = set()
    out: list[str] = []
    for raw_block in blocks:
        block = str(raw_block or "").strip()
        if not block:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", block.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(block)
    return "\n\n".join(out).strip()


def dedupe_repeated_long_lines(text: str) -> str:
    src = str(text or "").replace("\r", "")
    if not src:
        return ""
    seen: set[str] = set()
    out: list[str] = []
    for raw_line in src.split("\n"):
        line = str(raw_line or "")
        key = re.sub(r"[^a-z0-9]+", " ", line.lower()).strip()
        is_long_content = len(key) >= 36
        if is_long_content:
            if key in seen:
                continue
            seen.add(key)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def build_rules_context_block(rules_context: dict[str, Any] | None) -> str:
    ctx = rules_context if isinstance(rules_context, dict) else {}
    framework = str(ctx.get("playstyle_framework", "") or "").strip().lower()
    playstyle = str(ctx.get("playstyle", "") or "").strip().lower()
    if framework == "freeform":
        return (
            f"Playstyle framework: freeform ({playstyle or 'custom'})\n"
            "- Resolve through narrative consistency, clues, and social/emotional realism only.\n"
            "- Do NOT use DCs, dice, saving throws, ability checks, or D&D-style mechanical resolution.\n"
            "- Do NOT tie outcomes to character sheet stats, levels, or proficiency as mechanics (story color only).\n"
            "- Gear and inventory may appear as flavor; omit mechanical stats in wizard output.\n"
            "- The rules_mode and resolution_mode fields in this request are inactive; ignore them entirely."
        )

    rules_mode = str(ctx.get("rules_mode", "5e-style") or "5e-style").strip()
    resolution_mode = str(ctx.get("resolution_mode", "dice") or "dice").strip()
    return (
        f"Playstyle framework: rules_based (playstyle: {playstyle or 'adventure'})\n"
        f"Rules mode: {rules_mode}\n"
        f"Resolution mode: {resolution_mode}\n"
        "Use 5e-style framing for checks when the wizard step involves risk or uncertainty "
        "(per resolution mode: dice asks for rolls; narrative hides DC but still uses internal "
        "DC logic as guidance)."
    )


def build_character_context_block(rules_context: dict[str, Any] | None) -> str:
    ctx = rules_context if isinstance(rules_context, dict) else {}
    level = max(1, int(ctx.get("level") or 1))
    parts = [f"Character level: {level}"]
    for key, label in (
        ("character_class", "Class"),
        ("race", "Race"),
        ("background", "Background"),
    ):
        value = str(ctx.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


def build_backstory_anchor_block(rules_context: dict[str, Any] | None) -> str:
    """Repeat backstory for inventory/gear steps when worldInformation omits it."""
    ctx = rules_context if isinstance(rules_context, dict) else {}
    backstory = str(ctx.get("background") or "").strip()
    if not backstory:
        return ""
    clipped = backstory[:12000]
    return (
        "Character backstory (anchor inventory/gear to items and equipment explicitly "
        f"mentioned here when sensible):\n{clipped}\n\n"
    )


_DICE_DAMAGE_RE = re.compile(r"\d+\s*d\s*\d+(?:\s*[+-]\s*\d+)?", re.IGNORECASE)

_DEFAULT_DAMAGE_BY_WEAPON_HINT: list[tuple[str, str]] = [
    ("dagger", "1d4"),
    ("knife", "1d4"),
    ("rapier", "1d8"),
    ("shortsword", "1d6"),
    ("sword", "1d8"),
    ("longsword", "1d8"),
    ("mace", "1d6"),
    ("club", "1d4"),
    ("staff", "1d6"),
    ("quarterstaff", "1d6"),
    ("spear", "1d8"),
    ("bow", "1d8"),
    ("crossbow", "1d10"),
    ("axe", "1d8"),
    ("hammer", "1d6"),
    ("pistol", "1d10"),
    ("rifle", "2d6"),
    ("whip", "1d4"),
    ("sling", "1d4"),
]


def normalize_weapon_damage(damage: str, *, weapon_name: str = "", weapon_type: str = "") -> str:
    """Coerce LLM flat numbers (e.g. dmg 12) into dice notation for starter gear."""
    raw = str(damage or "").strip()
    match = _DICE_DAMAGE_RE.search(raw)
    if match:
        return re.sub(r"\s+", "", match.group(0))
    hint = f"{weapon_name} {weapon_type}".lower()
    for token, dice in _DEFAULT_DAMAGE_BY_WEAPON_HINT:
        if token in hint:
            return dice
    return "1d8"


def normalize_gear_json(gear: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(gear, dict):
        return gear
    out = dict(gear)
    weapon = dict(out.get("weapon") or {})
    if weapon:
        weapon["damage"] = normalize_weapon_damage(
            str(weapon.get("damage") or ""),
            weapon_name=str(weapon.get("name") or ""),
            weapon_type=str(weapon.get("weapon_type") or ""),
        )
        out["weapon"] = weapon
    return out


def gear_weapon_damage_looks_valid(gear: dict[str, Any]) -> bool:
    weapon = gear.get("weapon") if isinstance(gear, dict) else None
    if not isinstance(weapon, dict):
        return False
    return bool(_DICE_DAMAGE_RE.search(str(weapon.get("damage") or "")))


def wizard_context_authority_preamble() -> str:
    return (
        "AUTHORITY / PRIORITY:\n"
        "- Long cumulative setting or world information, rules snapshots, and earlier wizard chat are BACKGROUND CONTEXT ONLY.\n"
        "- The player's latest message (Request) and the current draft (JSON or text marked current/canonical) are AUTHORITATIVE.\n"
        "- Never let setting context override an explicit player instruction or an established fact in the current draft.\n"
        "- If anything in the context conflicts with the player or the draft, follow the player and the draft.\n\n"
    )


def prose_has_three_options_at(text: str, start: int) -> bool:
    opt_start = clamp_option_start(start)
    lower = str(text or "").lower()
    return (
        f"option {opt_start}" in lower
        and f"option {opt_start + 1}" in lower
        and f"option {opt_start + 2}" in lower
    )


def prose_has_three_campaigns_at(text: str, start: int) -> bool:
    opt_start = clamp_option_start(start)
    lower = str(text or "").lower()
    return (
        f"campaign {opt_start}:" in lower
        and f"campaign {opt_start + 1}:" in lower
        and f"campaign {opt_start + 2}:" in lower
    )


def split_campaign_blocks(text: str) -> list[dict[str, Any]]:
    src = str(text or "")
    pattern = re.compile(
        r"Campaign\s+(\d+)\s*:\s*([^\n]+)\n([\s\S]*?)(?=\n\s*Campaign\s+\d+\s*:|$)",
        re.I,
    )
    out: list[dict[str, Any]] = []
    for match in pattern.finditer(src):
        out.append(
            {
                "number": int(match.group(1) or 0),
                "title": str(match.group(2) or "").strip(),
                "body": str(match.group(3) or "").strip(),
            }
        )
    return out


def renumber_world_campaign_batch_text(text: str, opt_start: int) -> str | None:
    start = clamp_option_start(opt_start)
    blocks = split_campaign_blocks(str(text or ""))
    if len(blocks) < 3:
        return None
    chosen = blocks[:3]
    formatted: list[str] = []
    for index, block in enumerate(chosen):
        formatted.append(
            f"Campaign {start + index}: {block['title']}\n{block['body']}".rstrip()
        )
    formatted.append(
        f"Choose one campaign number ({selection_hint_triple(start)}), send your own world concept, or ask for different proposals."
    )
    return "\n\n".join(formatted).strip()


def pick_selected_campaign(current_draft: str, player_request: str) -> dict[str, Any] | None:
    campaigns = split_campaign_blocks(current_draft)
    if not campaigns:
        return None

    req = str(player_request or "").lower()
    by_number = re.search(r"\b(?:campaign\s*)?(\d{1,3})\b", req)
    if by_number:
        number = int(by_number.group(1))
        for campaign in campaigns:
            if campaign["number"] == number:
                return campaign

    for campaign in campaigns:
        title = str(campaign.get("title", "") or "").lower()
        if title and title in req:
            return campaign
    return None


def inventory_items_look_like_placeholders(items: Any) -> bool:
    if not isinstance(items, list) or not items:
        return True
    placeholder = re.compile(r"^\{[a-zA-Z0-9_]+\}$")
    for item in items:
        if not isinstance(item, dict):
            return True
        for key in ("item_id", "name", "description", "usage", "rarity"):
            value = str(item.get(key, "") or "").strip()
            if value and placeholder.match(value):
                return True
    return False


def parse_portrait_sd_prompt_text(text: str) -> dict[str, str]:
    """Parse wizard Portrait tab combined Positive/Negative prompt block."""
    import re

    src = str(text or "").strip()
    if not src:
        return {"positive_prompt": "", "negative_prompt": ""}
    neg_match = re.search(r"\bNegative\b", src, flags=re.IGNORECASE)
    if not neg_match:
        positive_only = re.sub(r"^Positive\s*\n?", "", src, count=1, flags=re.IGNORECASE).strip()
        return {"positive_prompt": positive_only, "negative_prompt": ""}
    neg_idx = neg_match.start()
    positive_block = re.sub(r"^Positive\s*\n?", "", src[:neg_idx], count=1, flags=re.IGNORECASE).strip()
    negative_block = re.sub(r"^Negative\s*\n?", "", src[neg_idx:], count=1, flags=re.IGNORECASE).strip()
    return {"positive_prompt": positive_block, "negative_prompt": negative_block}


def merge_portrait_sd_prompts(
    theme_label: str,
    player_name: str,
    style_override: str,
    llm_subject: str,
    llm_neg_extra: str,
) -> dict[str, str]:
    name = str(player_name or "").strip()
    theme = str(theme_label or "").strip()
    style = str(style_override or "").strip()
    subject_core = str(llm_subject or "").strip()

    head_parts = [part for part in (name, theme, style) if part]
    positive_parts = [
        ", ".join(head_parts),
        PORTRAIT_SD_SUBJECT_PREFIX,
        subject_core,
        PORTRAIT_SD_SUBJECT_SUFFIX,
    ]
    positive_prompt = ", ".join(part for part in positive_parts if part)

    extra = str(llm_neg_extra or "").strip()
    negative_prompt = (
        f"{PORTRAIT_SD_NEGATIVE_BASE}, {extra}" if extra else PORTRAIT_SD_NEGATIVE_BASE
    )
    return {
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
    }


def format_world_options(campaigns: list[dict[str, Any]], opt_start: int) -> str:
    parts: list[str] = []
    for index, campaign in enumerate(campaigns[:3]):
        parts.append(
            f"Campaign {opt_start + index}: {campaign.get('title', '')}\n"
            f"{campaign.get('paragraph_1', '')}\n\n"
            f"{campaign.get('paragraph_2', '')}"
        )
    parts.append(
        f"Choose one campaign number ({selection_hint_triple(opt_start)}), "
        "send your own world concept, or ask for different proposals."
    )
    return "\n\n".join(parts).strip()


def format_backstory_options(options: list[dict[str, Any]], opt_start: int) -> str:
    parts: list[str] = []
    for index, option in enumerate(options[:3]):
        parts.append(
            f"Option {opt_start + index}: {option.get('title', '')}\n"
            f"{option.get('paragraph_1', '')}\n\n"
            f"{option.get('paragraph_2', '')}"
        )
    parts.append(
        f"Choose one option number ({selection_hint_triple(opt_start)}), "
        "send your own backstory, or ask for different proposals."
    )
    return "\n\n".join(parts).strip()


def format_inventory_options(options: list[dict[str, Any]], opt_start: int) -> str:
    """Player-facing inventory Suggest-3 text — never dump raw JSON in chat."""
    parts: list[str] = []
    shown = options[:3]
    for index, option in enumerate(shown):
        if not isinstance(option, dict):
            continue
        parts.append(f"Option {opt_start + index}: {option.get('title', '')}")
        for item in option.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            parts.append(
                f"- {item.get('name', '')} x{item.get('quantity', 1)} "
                f"[{item.get('usage', '')}] — {item.get('description', '')}"
            )
        currency = option.get("currency") if isinstance(option.get("currency"), list) else []
        if currency:
            parts.append(f"Currency: {', '.join(str(x) for x in currency)}")
        parts.append("")
    count = len(shown)
    if count >= 3:
        hint = (
            f"Choose one option ({selection_hint_triple(opt_start)}), "
            "send your own inventory, or ask for new options."
        )
    elif count == 2:
        hint = (
            f"Choose option {opt_start} or {opt_start + 1}, "
            "send your own inventory, or ask for new options."
        )
    elif count == 1:
        hint = f"Choose option {opt_start}, send your own inventory, or ask for new options."
    else:
        hint = "Send your own inventory or ask for new options."
    parts.append(hint)
    return "\n".join(parts).strip()


def try_format_inventory_options_json(raw: str, opt_start: int) -> str | None:
    data = parse_wizard_json_object(raw)
    if not isinstance(data, dict):
        return None
    options = data.get("options")
    if not isinstance(options, list) or not options:
        return None
    return format_inventory_options(options, opt_start)


def salvage_gear_options(raw: str) -> list[dict[str, Any]]:
    """Extract gear option objects even when the outer JSON wrapper is malformed."""
    src = repair_gear_options_jsonish(str(raw or ""))
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r'"title"\s*:\s*"', src):
        brace = src.rfind("{", 0, match.start())
        if brace < 0:
            continue
        obj = extract_json_object_at(src, brace)
        if not isinstance(obj, dict):
            continue
        title = str(obj.get("title") or "").strip()
        weapon = obj.get("weapon") if isinstance(obj.get("weapon"), dict) else None
        armor = obj.get("armor") if isinstance(obj.get("armor"), dict) else None
        if not title or not weapon or not armor:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append(obj)
    return options


def repair_gear_options_jsonish(text: str) -> str:
    """Fix common LLM truncation: armor object not closed before the next option."""
    s = repair_corrupted_jsonish(str(text or ""))
    # ..."]},{"title" -> ..."]}},{"title"
    s = re.sub(r'(\]\s*)\},\s*\{\s*"title"', r'\1}},{"title"', s)
    return s


def format_gear_options(options: list[dict[str, Any]], opt_start: int) -> str:
    """Player-facing gear Suggest-3 text — never dump raw JSON in chat."""
    parts: list[str] = []
    shown = options[:3]
    for index, option in enumerate(shown):
        if not isinstance(option, dict):
            continue
        weapon = option.get("weapon") if isinstance(option.get("weapon"), dict) else {}
        armor = option.get("armor") if isinstance(option.get("armor"), dict) else {}
        parts.append(f"Option {opt_start + index}: {option.get('title', '')}")
        parts.append(
            f"Weapon: {weapon.get('name', '')} ({weapon.get('damage', '')}) — {weapon.get('description', '')}"
        )
        parts.append(
            f"Armor: {armor.get('name', '')} (AC {armor.get('ac', '')}) — {armor.get('description', '')}"
        )
        parts.append("")
    count = len(shown)
    if count >= 3:
        hint = (
            f"Choose one option ({selection_hint_triple(opt_start)}), "
            "send your own gear, or ask for new options."
        )
    elif count == 2:
        hint = (
            f"Choose option {opt_start} or {opt_start + 1}, "
            "send your own gear, or ask for new options."
        )
    elif count == 1:
        hint = f"Choose option {opt_start}, send your own gear, or ask for new options."
    else:
        hint = "Send your own gear or ask for new options."
    parts.append(hint)
    return "\n".join(parts).strip()


def try_format_gear_options_json(raw: str, opt_start: int) -> str | None:
    repaired = repair_gear_options_jsonish(raw)
    data = parse_wizard_json_object(repaired)
    options: list[dict[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("options"), list):
        options = [opt for opt in data["options"] if isinstance(opt, dict)]
    if not options:
        options = salvage_gear_options(repaired)
    if not options:
        return None
    normalized: list[dict[str, Any]] = []
    for option in options[:3]:
        opt = dict(option)
        gear = normalize_gear_json(
            {
                "weapon": dict(opt.get("weapon") or {}),
                "armor": dict(opt.get("armor") or {}),
            }
        )
        opt["weapon"] = gear.get("weapon") or opt.get("weapon") or {}
        opt["armor"] = gear.get("armor") or opt.get("armor") or {}
        normalized.append(opt)
    return format_gear_options(normalized, opt_start)


def parse_gear_options_raw(raw: str) -> list[dict[str, Any]]:
    """Return gear option objects from valid or partially malformed LLM output."""
    repaired = repair_gear_options_jsonish(raw)
    data = parse_wizard_json_object(repaired)
    if isinstance(data, dict) and isinstance(data.get("options"), list):
        return [opt for opt in data["options"] if isinstance(opt, dict)]
    return salvage_gear_options(repaired)


def dialog_transcript(messages: list[dict[str, str]] | str | None) -> str:
    if isinstance(messages, str):
        return strip_ui_markers(messages)
    if not messages:
        return ""

    lines: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = strip_ui_markers(message.get("content", ""))
        if not content:
            continue
        label = "Player" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


# JS-name compatibility helpers for callers mirroring the original module.
dedupeParagraphBlocks = dedupe_paragraph_blocks
dedupeRepeatedLongLines = dedupe_repeated_long_lines
buildRulesContextBlock = build_rules_context_block
wizardContextAuthorityPreamble = wizard_context_authority_preamble
pickSelectedCampaign = pick_selected_campaign
splitCampaignBlocks = split_campaign_blocks
renumberWorldCampaignBatchText = renumber_world_campaign_batch_text
proseHasThreeCampaignsAt = prose_has_three_campaigns_at
proseHasThreeOptionsAt = prose_has_three_options_at
inventoryItemsLookLikePlaceholders = inventory_items_look_like_placeholders
mergePortraitSdPrompts = merge_portrait_sd_prompts
parseWizardJsonObject = parse_wizard_json_object
