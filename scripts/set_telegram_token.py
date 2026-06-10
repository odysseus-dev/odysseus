#!/usr/bin/env python3
"""Configure the overseer Telegram bot token (stored encrypted).

Usage:
    python scripts/set_telegram_token.py <bot-token>   # set + verify
    python scripts/set_telegram_token.py --status      # show binding state
    python scripts/set_telegram_token.py --unbind      # forget chat binding

After setting the token, restart odysseus and send /start to the bot —
the first chat to do so becomes the bound owner chat.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from services.telegram.bot import TelegramBridge

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]
    if arg == "--status":
        token = TelegramBridge.get_token()
        chat = TelegramBridge.get_chat_id()
        print(f"token: {'set (' + token[:8] + '...)' if token else 'NOT SET'}")
        print(f"chat_id: {chat or 'not bound (send /start to the bot)'}")
        return
    if arg == "--unbind":
        TelegramBridge._set_chat_id("")
        print("chat binding cleared; next /start re-binds")
        return

    token = arg.strip()
    import httpx
    resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
    data = resp.json()
    if not data.get("ok"):
        print(f"FAIL: token rejected by Telegram: {data.get('description')}")
        sys.exit(1)
    me = data["result"]
    TelegramBridge.set_token(token)
    print(f"OK: token saved (encrypted) for bot @{me.get('username')}")
    print("Restart odysseus, then send /start to the bot to bind this chat.")


if __name__ == "__main__":
    main()
