import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..enums import EventKind

logger = logging.getLogger(__name__)


class Reporter:
    """Sends each step to the API, and keeps a local copy next to the recording.

    A lost event must never stop a recording, so a failed POST is logged and dropped.
    """

    def __init__(self, api_url: str, meeting_id: str, token: str, out_dir: Path):
        self.path = f"/internal/meetings/{meeting_id}/events"
        self.client = httpx.AsyncClient(
            base_url=api_url, headers={"X-Internal-Token": token}, timeout=5
        )
        self.log_file = out_dir / "events.jsonl"

    async def event(self, kind: EventKind, **detail: Any) -> None:
        logger.info("event %s %s", kind, detail)
        with self.log_file.open("a") as log:
            line = {"at": datetime.now(UTC).isoformat(), "kind": kind, "detail": detail}
            log.write(json.dumps(line) + "\n")
        try:
            response = await self.client.post(self.path, json={"kind": kind, "detail": detail})
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning("could not report %s: %s", kind, error)

    async def aclose(self) -> None:
        await self.client.aclose()
