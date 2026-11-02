"""Pre-Wave-3 private_browser schema (agent-browser wrapper), used when ODYSSEUS_PRIVATE_BROWSER_LEGACY is on."""

PRIVATE_BROWSER_SCHEMA = {
        "type": "function",
        "function": {
            "name": "private_browser",
            "description": "Private browser automation through Odysseus' agent-browser wrapper. After open, snapshot the page and interact with returned element refs such as @e12; click/fill target is a selector or element ref, never guessed visible text. Prefer one batch for known consecutive steps, such as open plus snapshot. Use only when a specific page needs JavaScript, login/session state, interaction, or rendered DOM. For open-ended search use web_search; for reading a normal URL use web_fetch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["open", "read", "snapshot", "find", "evaluate", "click", "fill", "press", "scroll", "wait", "screenshot", "close", "batch"]},
                    "url": {"type": "string", "description": "Required URL for open; optional URL for read (omit to read the current page)"},
                    "selector": {"type": "string", "description": "Element ref or selector for read/click/fill/wait"},
                    "target": {"type": "string", "description": "Element ref returned by snapshot (preferred, e.g. @e12) or CSS selector for read/click/fill/wait; never a guessed visible label; top or bottom for scroll"},
                    "key": {"type": "string", "description": "Key name for press action, e.g. Enter"},
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "Direction for scroll action"},
                    "amount": {"type": "integer", "minimum": 1, "description": "Optional scroll distance in pixels; default 300"},
                    "text": {"type": "string", "description": "Text for fill action"},
                    "value": {"type": "string", "description": "Alternative text/value for fill action"},
                    "find": {"type": "string", "description": "Visible text to locate for find action"},
                    "script": {"type": "string", "description": "JavaScript expression for evaluate action"},
                    "path": {"type": "string", "description": "Optional output path for screenshot"},
                    "commands": {
                        "type": "array",
                        "description": "Non-empty batch commands as arrays, e.g. [[\"open\", \"https://example.com\"], [\"snapshot\"]]. Do not send an empty batch; use action=snapshot for current page state.",
                        "items": {
                            "oneOf": [
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "object"},
                            ]
                        },
                    },
                    "timeout_ms": {"type": "integer", "description": "Optional operation timeout, max 120000; for action=wait without a selector, this is the wait duration"}
                },
                "required": ["action"]
            }
        }
    }
