#!/usr/bin/env python3
"""Sync the LM Studio (Windows host) endpoint to the current WSL gateway IP.

The app does this automatically on startup; this script is the manual /
diagnostic version.

Usage:
    python scripts/sync_lmstudio_endpoint.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from src.lmstudio_sync import sync_lmstudio_endpoint, _candidate_ips, LMSTUDIO_PORT

    print(f"Candidate host IPs: {', '.join(_candidate_ips())} (port {LMSTUDIO_PORT})")
    base = asyncio.run(sync_lmstudio_endpoint())
    if base:
        print(f"OK: LM Studio reachable, endpoint synced to {base}")
    else:
        print("FAIL: LM Studio is not reachable on any candidate IP.")
        print("Check that the server is running and 'Serve on Local Network' is enabled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
