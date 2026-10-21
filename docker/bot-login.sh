#!/bin/sh
# One-time sign-in for signed-in bots. Starts the bot's own Chromium as a normal browser (not
# driven by Playwright, which Google refuses at sign-in) on a virtual screen, and shows that
# screen at http://localhost:6080/vnc.html. The profile it signs into is what bots start from.
set -eu
label="${BOT_ACCOUNT_LABEL:?Set BOT_ACCOUNT_LABEL in .env, e.g. BOT_ACCOUNT_LABEL=notetaker}"
profile="/data/accounts/$label/profile"
mkdir -p "$profile"
chown -R bot:bot "/data/accounts"

export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/dev/null 2>&1 &
sleep 1
# The VNC server listens inside the container only; websockify is the one published port.
x11vnc -display :99 -forever -shared -nopw -localhost -quiet >/dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &

chromium="$(find /ms-playwright -path '*/chromium-*/chrome-linux*/chrome' -type f | head -n 1)"
echo "Open http://localhost:6080/vnc.html and sign in to the bot's Google account."
echo "Then check meet.google.com shows the account, close the browser window, and stop this."
# --password-store=basic: Playwright launches Chromium the same way, so the bots can read
# the cookies this browser writes.
exec setpriv --reuid=bot --regid=bot --init-groups env HOME=/home/bot \
    "$chromium" --user-data-dir="$profile" --password-store=basic --no-first-run \
    --no-default-browser-check --lang=en-US --window-position=0,0 --window-size=1280,800 \
    https://accounts.google.com/
