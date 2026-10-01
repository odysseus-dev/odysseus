"""
desktop_server.py

MCP server that lets the agent see and control the host's desktop: take
screenshots, move/click/drag the mouse, scroll, type text, and press keys.

This is the native-desktop counterpart to the Playwright browser server. It
only makes sense on a native install (not Docker), and it is OFF by default —
set ODYSSEUS_DESKTOP_CONTROL=1 to register it (see src/builtin_mcp.py).

Coordinate model
----------------
Screenshots are downscaled so the longest side fits
ODYSSEUS_DESKTOP_MAX_SIZE (default 1366px). Every coordinate the model sends
is in *screenshot pixels* of the most recent screenshot; the server maps it
back to real screen pixels. This keeps images cheap for the model while
clicks still land where the model saw the target.

Safety
------
- pyautogui's FAILSAFE is on: slam the mouse into any screen corner to abort
  the current action.
- Every action tool returns a fresh screenshot, so the model always acts on
  what is actually on screen rather than a guess.
- Screen content is untrusted: the agent loop wraps it as such, and the
  agent's approval gate asks the user before side-effecting actions once
  untrusted content has been seen (see src/tool_capabilities.py).
"""

import asyncio
import base64
import contextlib
import io
import os
import sys
import time

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent, TextContent, Tool

server = Server("desktop")

_MAX_SIZE = max(320, int(os.environ.get("ODYSSEUS_DESKTOP_MAX_SIZE", "1366") or 1366))
_SETTLE_S = float(os.environ.get("ODYSSEUS_DESKTOP_SETTLE_SECONDS", "0.6") or 0.6)
_MAX_TYPE_CHARS = 5000

# Geometry of the most recent screenshot: real-screen origin + scale factor.
# Coordinates from the model are divided by `scale` and offset by the origin.
_view = {"left": 0, "top": 0, "width": 0, "height": 0, "scale": 1.0, "monitor": 1}

_pyautogui = None
_mss = None
_import_error = ""


def _load_backends() -> bool:
    """Import the GUI backends lazily so the server can still start (and
    report a clear error) on a headless box or without the optional deps."""
    global _pyautogui, _mss, _import_error
    if _pyautogui is not None and _mss is not None:
        return True
    try:
        import mss  # noqa: F401
        import pyautogui
        from PIL import Image  # noqa: F401
    except Exception as e:  # ImportError, or DISPLAY errors on Linux
        _import_error = (
            f"Desktop control is unavailable: {type(e).__name__}: {e}. "
            "Install with: pip install mss pyautogui pillow "
            "(and run Odysseus natively, not in Docker)."
        )
        return False
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _pyautogui = pyautogui
    _mss = mss
    return True


# ── Screenshots ──────────────────────────────────────────────────────────


def _grab(monitor: int | None = None) -> tuple[str, str]:
    """Capture a monitor, downscale it, remember the geometry, and return
    (base64 png, human summary)."""
    from PIL import Image

    # mss >= 10 renamed the factory to MSS (mss.mss is deprecated).
    with getattr(_mss, "MSS", _mss.mss)() as sct:
        monitors = sct.monitors  # [0] = all monitors combined, [1..] = each
        idx = _view["monitor"] if monitor is None else int(monitor)
        if idx < 0 or idx >= len(monitors):
            raise ValueError(
                f"monitor {idx} does not exist; valid: 0 (all) or 1..{len(monitors) - 1}"
            )
        mon = monitors[idx]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    scale = min(1.0, _MAX_SIZE / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.LANCZOS,
        )
    _view.update(
        left=mon["left"], top=mon["top"], width=img.width, height=img.height,
        scale=scale, monitor=idx,
    )

    # Draw the cursor so the model can see where the mouse is.
    try:
        from PIL import ImageDraw

        cx, cy = _pyautogui.position()
        sx, sy = (cx - mon["left"]) * scale, (cy - mon["top"]) * scale
        if 0 <= sx < img.width and 0 <= sy < img.height:
            d = ImageDraw.Draw(img)
            d.ellipse((sx - 6, sy - 6, sx + 6, sy + 6), outline=(255, 0, 0), width=2)
            d.line((sx - 10, sy, sx + 10, sy), fill=(255, 0, 0), width=1)
            d.line((sx, sy - 10, sx, sy + 10), fill=(255, 0, 0), width=1)
    except Exception:
        pass

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    summary = (
        f"Screenshot of monitor {idx}: {img.width}x{img.height} "
        f"(coordinates for actions are in these screenshot pixels; the red "
        f"circle marks the mouse cursor)."
    )
    return base64.b64encode(buf.getvalue()).decode("ascii"), summary


def _to_screen(x, y) -> tuple[int, int]:
    """Map screenshot-space coordinates to real screen pixels."""
    if not _view["width"]:
        raise ValueError("Take a desktop_screenshot first so coordinates have a reference.")
    x, y = float(x), float(y)
    if not (0 <= x <= _view["width"] and 0 <= y <= _view["height"]):
        raise ValueError(
            f"({x:g}, {y:g}) is outside the last screenshot "
            f"({_view['width']}x{_view['height']})."
        )
    s = _view["scale"] or 1.0
    return round(_view["left"] + x / s), round(_view["top"] + y / s)


def _to_view(sx, sy) -> tuple[int, int]:
    s = _view["scale"] or 1.0
    return round((sx - _view["left"]) * s), round((sy - _view["top"]) * s)


# ── Text input ───────────────────────────────────────────────────────────


_win_input = None


def _win_input_api():
    """Build the Win32 SendInput structures once and cache them."""
    global _win_input
    if _win_input is None:
        import ctypes
        from ctypes import wintypes

        ulong_ptr = ctypes.c_size_t

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ulong_ptr)]

        class MOUSEINPUT(ctypes.Structure):  # largest union member; sets INPUT's size
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        _win_input = (ctypes, INPUT, KEYBDINPUT, ctypes.windll.user32.SendInput)
    return _win_input


def _type_unicode_windows(text: str) -> None:
    """Type arbitrary Unicode on Windows via SendInput(KEYEVENTF_UNICODE);
    pyautogui.write only handles characters on a US keyboard layout. Each
    line is sent as one batched SendInput call; newlines press Enter."""
    ctypes, INPUT, KEYBDINPUT, send_input = _win_input_api()
    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP, INPUT_KEYBOARD = 0x0004, 0x0002, 1

    lines = text.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if i:
            _pyautogui.press("enter")
        data = line.encode("utf-16-le")
        units = [int.from_bytes(data[j:j + 2], "little") for j in range(0, len(data), 2)]
        if not units:
            continue
        events = (INPUT * (2 * len(units)))()
        for k, unit in enumerate(units):
            for n, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                ev = events[2 * k + n]
                ev.type = INPUT_KEYBOARD
                ev.u.ki = KEYBDINPUT(0, unit, flags, 0, 0)
        sent = send_input(len(events), events, ctypes.sizeof(INPUT))
        if sent != len(events):
            raise OSError(f"SendInput delivered {sent}/{len(events)} key events (input may be blocked by a higher-privilege window).")


def _type_text(text: str) -> None:
    if sys.platform == "win32":
        _type_unicode_windows(text)
    elif text.isascii():
        _pyautogui.write(text, interval=0.01)
    else:
        raise ValueError("Non-ASCII typing is only supported on Windows; paste it instead.")


_KEY_ALIASES = {
    "return": "enter", "esc": "escape", "del": "delete", "ins": "insert",
    "control": "ctrl", "cmd": "win" if sys.platform == "win32" else "command",
    "super": "win", "windows": "win", "meta": "win" if sys.platform == "win32" else "command",
    "option": "alt", "page_up": "pageup", "page_down": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "spacebar": "space",
}


def _parse_keys(combo: str) -> list[str]:
    keys = []
    for part in combo.replace(" ", "").split("+"):
        k = part.strip().lower()
        if not k:
            continue
        k = _KEY_ALIASES.get(k, k)
        if k not in _pyautogui.KEYBOARD_KEYS:
            raise ValueError(f"Unknown key {part!r}.")
        keys.append(k)
    if not keys:
        raise ValueError("No keys given.")
    return keys


# ── Tools ────────────────────────────────────────────────────────────────

_XY = {
    "x": {"type": "number", "description": "X in pixels of the latest screenshot"},
    "y": {"type": "number", "description": "Y in pixels of the latest screenshot"},
}

TOOLS = [
    Tool(
        name="desktop_screenshot",
        description=(
            "Capture the user's screen so you can see it. Call this first, and "
            "whenever you need to re-check the screen. All action coordinates "
            "refer to pixels in the most recent screenshot."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "monitor": {
                    "type": "integer",
                    "description": "Monitor number (1 = primary, 0 = all monitors). Defaults to the last one used.",
                },
            },
        },
    ),
    Tool(
        name="desktop_click",
        description="Move the mouse to (x, y) and click. Use clicks=2 for a double-click, button='right' for a context menu.",
        inputSchema={
            "type": "object",
            "properties": {
                **_XY,
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                "modifiers": {"type": "string", "description": "Keys held during the click, e.g. 'ctrl' or 'shift+ctrl'"},
            },
            "required": ["x", "y"],
        },
    ),
    Tool(
        name="desktop_move_mouse",
        description="Move the mouse to (x, y) without clicking (e.g. to reveal a hover tooltip).",
        inputSchema={"type": "object", "properties": dict(_XY), "required": ["x", "y"]},
    ),
    Tool(
        name="desktop_drag",
        description="Press the left button at (start_x, start_y), drag to (x, y), and release.",
        inputSchema={
            "type": "object",
            "properties": {
                "start_x": {"type": "number"}, "start_y": {"type": "number"}, **_XY,
            },
            "required": ["start_x", "start_y", "x", "y"],
        },
    ),
    Tool(
        name="desktop_scroll",
        description="Scroll at (x, y). amount is in wheel notches (default 3).",
        inputSchema={
            "type": "object",
            "properties": {
                **_XY,
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "minimum": 1, "maximum": 30, "default": 3},
            },
            "required": ["x", "y", "direction"],
        },
    ),
    Tool(
        name="desktop_type",
        description="Type text into whatever currently has keyboard focus. Click the target field first.",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    Tool(
        name="desktop_key",
        description=(
            "Press a key or key combination, e.g. 'enter', 'escape', 'tab', "
            "'ctrl+c', 'alt+tab', 'win+r', 'ctrl+shift+esc'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "keys": {"type": "string"},
                "repeat": {"type": "integer", "minimum": 1, "maximum": 50, "default": 1},
            },
            "required": ["keys"],
        },
    ),
    Tool(
        name="desktop_cursor_position",
        description="Return the mouse position in screenshot coordinates.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="desktop_wait",
        description="Wait for the screen to update (e.g. an app launching), then take a screenshot.",
        inputSchema={
            "type": "object",
            "properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 30, "default": 2}},
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@contextlib.contextmanager
def _held(modifiers: str | None):
    """Hold modifier keys (e.g. 'ctrl+shift') for the duration of a mouse action."""
    keys = _parse_keys(modifiers) if modifiers else []
    for k in keys:
        _pyautogui.keyDown(k)
    try:
        yield
    finally:
        for k in reversed(keys):
            _pyautogui.keyUp(k)


def _run(name: str, args: dict) -> tuple[str, bool]:
    """Execute one tool synchronously. Returns (message, take_screenshot)."""
    pg = _pyautogui
    if name == "desktop_screenshot":
        return "", True
    if name == "desktop_cursor_position":
        if not _view["width"]:
            raise ValueError("Take a desktop_screenshot first so coordinates have a reference.")
        cx, cy = pg.position()
        vx, vy = _to_view(cx, cy)
        return f"Cursor at ({vx}, {vy}) in screenshot coordinates.", False
    if name == "desktop_wait":
        time.sleep(max(0.0, min(30.0, float(args.get("seconds", 2)))))
        return "Waited.", True
    if name == "desktop_click":
        sx, sy = _to_screen(args["x"], args["y"])
        button = args.get("button", "left")
        if button not in ("left", "right", "middle"):
            raise ValueError("button must be left, right, or middle")
        clicks = max(1, min(3, int(args.get("clicks", 1))))
        with _held(args.get("modifiers")):
            pg.click(sx, sy, clicks=clicks, interval=0.08, button=button)
        return f"{button.capitalize()}-clicked x{clicks} at ({args['x']}, {args['y']}).", True
    if name == "desktop_move_mouse":
        sx, sy = _to_screen(args["x"], args["y"])
        pg.moveTo(sx, sy, duration=0.1)
        return f"Moved mouse to ({args['x']}, {args['y']}).", True
    if name == "desktop_drag":
        x0, y0 = _to_screen(args["start_x"], args["start_y"])
        x1, y1 = _to_screen(args["x"], args["y"])
        pg.moveTo(x0, y0, duration=0.1)
        pg.mouseDown(button="left")
        try:
            pg.moveTo(x1, y1, duration=0.4)
        finally:
            pg.mouseUp(button="left")
        return f"Dragged ({args['start_x']}, {args['start_y']}) -> ({args['x']}, {args['y']}).", True
    if name == "desktop_scroll":
        sx, sy = _to_screen(args["x"], args["y"])
        amount = max(1, min(30, int(args.get("amount", 3))))
        direction = args.get("direction")
        pg.moveTo(sx, sy, duration=0.05)
        if direction in ("up", "down"):
            pg.scroll(amount * 120 * (1 if direction == "up" else -1))
        elif direction in ("left", "right"):
            pg.hscroll(amount * 120 * (1 if direction == "right" else -1))
        else:
            raise ValueError("direction must be up, down, left, or right")
        return f"Scrolled {direction} {amount} at ({args['x']}, {args['y']}).", True
    if name == "desktop_type":
        text = str(args.get("text", ""))
        if len(text) > _MAX_TYPE_CHARS:
            raise ValueError(f"text is longer than {_MAX_TYPE_CHARS} characters")
        _type_text(text)
        return f"Typed {len(text)} characters.", True
    if name == "desktop_key":
        keys = _parse_keys(str(args.get("keys", "")))
        repeat = max(1, min(50, int(args.get("repeat", 1))))
        for _ in range(repeat):
            pg.hotkey(*keys, interval=0.03)
        return f"Pressed {'+'.join(keys)}" + (f" x{repeat}." if repeat > 1 else "."), True
    raise ValueError(f"Unknown tool: {name}")


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    # Raised exceptions become isError results in the MCP SDK, which the
    # agent loop surfaces as a failed tool call rather than normal output.
    if not _load_backends():
        raise RuntimeError(_import_error)
    args = arguments or {}
    try:
        msg, want_shot = await asyncio.to_thread(_run, name, args)
    except _pyautogui.FailSafeException:
        raise RuntimeError(
            "Aborted: the user moved the mouse into a screen corner (failsafe). "
            "Stop and ask the user before continuing."
        ) from None
    if not want_shot:
        return [TextContent(type="text", text=msg)]
    if name not in ("desktop_screenshot", "desktop_wait") and _SETTLE_S > 0:
        await asyncio.sleep(_SETTLE_S)
    b64, summary = await asyncio.to_thread(
        _grab, args.get("monitor") if name == "desktop_screenshot" else None
    )
    return [
        TextContent(type="text", text=f"{msg} {summary}".strip()),
        ImageContent(type="image", data=b64, mimeType="image/png"),
    ]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
