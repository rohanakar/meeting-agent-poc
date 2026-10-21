"""Starts and stops bot containers on the Docker host the worker runs on."""

import contextlib
import os
from pathlib import Path

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from ..config import Settings, meeting_dir
from ..enums import EgressMode
from ..models import Meeting
from ..platforms import join_url

# The bot runs as this user (uid 1000 in the image); it must be able to write the meeting folder.
BOT_USER = "bot"
BOT_UID = 1000
# The image's default command is the API; the entrypoint starts Xvfb and PulseAudio first.
BOT_COMMAND = ["bot-entrypoint", "python", "-m", "meeting_agent.bot.main"]


def start_bot(
    meeting: Meeting,
    settings: Settings,
    notice_audio: Path | None,
    profile_dir: Path | None,
) -> Container:
    out_dir = meeting_dir(meeting.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    # The worker runs as root in compose; a local run as a normal user cannot and need not chown.
    with contextlib.suppress(PermissionError):
        os.chown(out_dir, BOT_UID, BOT_UID)

    if meeting.egress_mode == EgressMode.TUNNEL:
        # Inside the Tailscale container's network, every packet leaves through the exit node.
        network = {"network_mode": f"container:{settings.tailscale_container}"}
    else:
        network = {"network": settings.docker_network}
    environment = {
        "BOT_MEETING_ID": str(meeting.id),
        "BOT_PLATFORM": meeting.platform,
        "BOT_JOIN_URL": join_url(meeting.meeting_url),
        "BOT_DISPLAY_NAME": settings.bot_display_name,
        "BOT_EGRESS_MODE": meeting.egress_mode,
        "BOT_PROXY_URL": settings.egress_proxy_url
        if meeting.egress_mode == EgressMode.PROXY
        else "",
        "BOT_PROFILE_DIR": str(profile_dir or ""),
        "BOT_NOTICE_TEXT": settings.notice_text,
        "BOT_NOTICE_AUDIO": str(notice_audio or ""),
        "BOT_ADMISSION_TIMEOUT_S": str(settings.admission_timeout_s),
        "BOT_ALONE_TIMEOUT_S": str(settings.alone_timeout_s),
        "BOT_MAX_MEETING_S": str(settings.max_meeting_s),
        "BOT_API_URL": settings.api_internal_url,
        "BOT_INTERNAL_TOKEN": settings.internal_token,
        "BOT_OUT_DIR": str(out_dir),
    }
    return docker.from_env().containers.run(
        settings.bot_image,
        BOT_COMMAND,
        user=BOT_USER,
        detach=True,
        name=f"meeting-bot-{meeting.id.hex[:12]}",
        labels={"meeting-agent.meeting-id": str(meeting.id)},
        environment=environment,
        # Same volume, same mount point: paths in the environment mean the same file in both.
        volumes={settings.data_volume: {"bind": str(settings.data_dir), "mode": "rw"}},
        shm_size="1g",
        **network,
    )


def stop_container(container_id: str) -> None:
    with contextlib.suppress(NotFound):
        # 30 s: the bot stops the recorder, which writes the file's end, then leaves the call.
        docker.from_env().containers.get(container_id).stop(timeout=30)
