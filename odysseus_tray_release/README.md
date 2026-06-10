# Odysseus Tray

A KDE system tray controller for [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) — start, stop, and manage local AI models without touching a terminal.

Built for Linux (KDE/Wayland). Uses PyQt6.

---

## Features

- Start and stop the full Odysseus stack (ChromaDB + Ollama + Odysseus) from the tray
- Opens the browser automatically when Odysseus is ready
- Color-coded icon: gray = stopped, orange = starting, green = running, red = stopping
- Load any downloaded Ollama model from a submenu
- See what models are currently loaded in VRAM
- Kill Model button that force-kills llama-server and frees VRAM instantly
- Desktop icon for one-click launch

---

## Requirements

- [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) installed at `~/odysseus`
- Python 3.11+ with the Odysseus venv set up
- [Ollama](https://ollama.com) installed
- [ChromaDB](https://www.trychroma.com) installed (`pip install chromadb`)
- PyQt6 (`pip install PyQt6` inside the Odysseus venv)

---

## Install

```bash
# copy the tray script into your Odysseus directory
cp odysseus_tray.py ~/odysseus/

# install PyQt6 into the Odysseus venv
cd ~/odysseus
source venv/bin/activate
pip install PyQt6

# install the system tray autostart entry
cp odysseus-tray.desktop ~/.config/autostart/

# install the desktop icon
cp Odysseus.desktop ~/Desktop/
chmod +x ~/Desktop/Odysseus.desktop
```

Then either double-click the desktop icon or log out and back in for autostart.

---

## Usage

- **Left click** — open Odysseus in browser (when running)
- **Right click** — full menu
  - Start / Stop Odysseus
  - Load Model — lists all downloaded Ollama models, click to swap
  - Running Models — shows what's loaded in VRAM, includes Kill Model button
  - Quit — closes the tray app

---

## Notes

- Ollama is started with `OLLAMA_KEEP_ALIVE=0` so models dump VRAM as soon as they go idle
- Kill Model force-kills both `ollama` and `llama-server` processes for immediate VRAM release
- Any models pulled via `ollama pull` will automatically appear in the Load Model submenu
