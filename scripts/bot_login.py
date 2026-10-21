"""Signs a Google account in once, so Meet bots can join as that account.

A person signs in by hand in the window this opens. The script saves the browser session
(Playwright storage state), encrypted with FERNET_KEY, as <out>/<label>.enc.

Run it on the machine behind the exit node, so Google sees the sign-in come from the same
address the bots will use. Then copy the file into the data volume (see the README).

    uv run --extra bot python scripts/bot_login.py --label notetaker1
"""

import argparse
import json
from pathlib import Path

from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

from meeting_agent.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", required=True, help="Name for this account, e.g. notetaker1")
    parser.add_argument("--out", type=Path, default=Path("data/accounts"))
    parser.add_argument(
        "--channel",
        default="chrome",
        help="chrome uses the installed Google Chrome, which Google's sign-in accepts more often "
        "than Playwright's Chromium; use chromium if Chrome is not installed",
    )
    args = parser.parse_args()

    key = get_settings().fernet_key
    if not key:
        example = Fernet.generate_key().decode()
        raise SystemExit(f"Set FERNET_KEY in .env first, for example:\nFERNET_KEY={example}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=None if args.channel == "chromium" else args.channel,
            headless=False,
            args=["--lang=en-US", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        page.goto("https://accounts.google.com/")
        input("Sign in to the bot's Google account in the browser window, then press Enter here. ")
        page.goto("https://meet.google.com/")
        input("Check that Meet shows the account as signed in, then press Enter to save. ")
        state = context.storage_state()
        browser.close()

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.label}.enc"
    path.write_bytes(Fernet(key).encrypt(json.dumps(state).encode()))
    print(f"Saved {path}. Set BOT_ACCOUNT_LABEL={args.label} and copy the file to the server.")


if __name__ == "__main__":
    main()
