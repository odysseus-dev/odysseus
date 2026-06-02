#!/usr/bin/env python3
"""Pair the odysseus-mobile app from the terminal.

Mints a chat-scoped API token and prints the host / port / token plus an ASCII
QR code you can scan with the odysseus-mobile app.

Usage (from the repo root, with the venv active):
    python scripts/pair_mobile.py
    python scripts/pair_mobile.py --owner alice --port 7860 --name "my phone"

NOTE: if the Odysseus server is ALREADY running, prefer the in-app pairing page
at  http://localhost:<port>/api/companion/pair  (open it in the browser where
you're logged in as admin). That mints the token in-process and the server
accepts it instantly. A token minted by THIS script while the server is running
won't be recognized until the server's token cache refreshes — restart the
server, or just mint a fresh token from the pairing page instead.
"""

import argparse
import json
import sys

# Ensure repo root is importable when run as `python scripts/pair_mobile.py`.
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from companion import pairing  # noqa: E402


def _ascii_qr(payload: dict) -> None:
    try:
        import qrcode
    except ImportError:
        print("(install `qrcode` to render a scannable QR here)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(json.dumps(payload, separators=(",", ":")))
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pair the odysseus-mobile app.")
    parser.add_argument("--owner", help="Token owner username (default: an admin from auth.json)")
    parser.add_argument("--port", type=int, default=None, help="Port the phone should connect to")
    parser.add_argument("--host", help="Override the LAN host/IP the phone should use")
    parser.add_argument("--name", default="odysseus-mobile", help="Label for the token")
    args = parser.parse_args()

    owner = args.owner or pairing.find_admin_user()
    if not owner:
        print("ERROR: no users found in data/auth.json. Start Odysseus once to create an admin.",
              file=sys.stderr)
        return 1

    hosts = pairing.lan_ip_candidates()
    host = args.host or (hosts[0] if hosts else "127.0.0.1")
    port = args.port or pairing.default_port()

    token_id, raw_token = pairing.mint_token(owner, name=args.name)
    payload = pairing.pairing_payload(host, port, raw_token)

    print()
    print("  Odysseus Mobile — pairing code")
    print("  ──────────────────────────────")
    print(f"  owner : {owner}")
    print(f"  host  : {host}")
    if len(hosts) > 1:
        print(f"          (other addresses: {', '.join(hosts[1:])})")
    print(f"  port  : {port}")
    print(f"  token : {raw_token}")
    print(f"  id    : {token_id}  (revoke in Settings → API tokens)")
    print()
    print(f"  payload: {json.dumps(payload, separators=(',', ':'))}")
    print()
    _ascii_qr(payload)
    print()
    print("  Scan the QR in odysseus-mobile, or type host/port/token manually.")
    print("  The server must bind to your LAN (APP_BIND=0.0.0.0 / --host 0.0.0.0)")
    print("  and the phone must be on the same Wi-Fi.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
