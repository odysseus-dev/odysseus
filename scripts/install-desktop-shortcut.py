#!/usr/bin/env python3
"""Install a Linux menu shortcut for an existing Odysseus server."""

import argparse
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="The URL you use to open Odysseus")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("This installer is for Linux.")
    try:
        url = urlsplit(args.url)
        valid = url.scheme in ("http", "https") and bool(url.hostname)
        url.port
    except ValueError:
        valid = False
    if not valid or any(char.isspace() or ord(char) < 32 for char in args.url):
        parser.error("Provide an HTTP or HTTPS URL without whitespace.")
    if url.username is not None or url.password is not None:
        parser.error("Do not include credentials in the shortcut URL.")
    if not shutil.which("xdg-open"):
        parser.error("Install xdg-utils before installing the shortcut.")

    data_dir = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    if not data_dir.is_absolute():
        parser.error("XDG_DATA_HOME must be an absolute path.")
    icon = Path(__file__).resolve().parents[1] / "static/icons/icon-512.png"
    if not icon.is_file():
        parser.error("Run this installer from an Odysseus source checkout.")
    icon_dir = data_dir / "icons/hicolor/512x512/apps"
    applications = data_dir / "applications"
    icon_dir.mkdir(parents=True, exist_ok=True)
    applications.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon, icon_dir / "odysseus.png")
    # Exec uses desktop-entry quoting and treats percent signs as field codes.
    target = args.url.replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        target = target.replace(char, "\\" + char)
    entry = applications / "odysseus.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Odysseus\n"
        "GenericName=Local AI Workspace\n"
        "Comment=Chat with local models and use agents, documents and research tools\n"
        f'Exec=xdg-open "{target}"\n'
        "Icon=odysseus\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Keywords=AI;LLM;Chat;Assistant;Agent;Ollama;Local;Research;Documents;\n",
        encoding="utf-8",
    )
    print(f"Installed {entry}")


if __name__ == "__main__":
    main()
