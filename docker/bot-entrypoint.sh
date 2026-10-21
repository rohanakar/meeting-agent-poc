#!/bin/sh
set -e

export DISPLAY=:99
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/dev/null 2>&1 &

pulseaudio --daemonize=yes --exit-idle-time=-1 --disallow-exit --log-target=stderr
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    pactl info >/dev/null 2>&1 && break
    sleep 0.3
done

# An idle sink is suspended and its monitor then delivers nothing, so a meeting where nobody
# speaks gave an empty file. Without suspension the recorder always gets audio, silence included.
pactl unload-module module-suspend-on-idle >/dev/null 2>&1 || true

# Everything the meeting plays goes to meeting_out; the recorder reads meeting_out.monitor.
pactl load-module module-null-sink sink_name=meeting_out \
    sink_properties=device.description=meeting_out >/dev/null
pactl set-default-sink meeting_out

# The spoken notice is played into notice_in. Chromium's microphone (bot_mic) is a real source
# made from notice_in's monitor, because browsers skip monitor sources when they pick a mic.
pactl load-module module-null-sink sink_name=notice_in \
    sink_properties=device.description=notice_in >/dev/null
pactl load-module module-remap-source source_name=bot_mic master=notice_in.monitor \
    source_properties=device.description=bot_mic >/dev/null
pactl set-default-source bot_mic

exec "$@"
