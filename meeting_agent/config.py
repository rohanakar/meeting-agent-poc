from functools import cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .enums import EgressMode

DEFAULT_NOTICE = (
    "Hello, I am Lexapaar's AI notetaker. I will be recording this conversation to generate a "
    "summary. If anyone does not consent, please remove me from the call. "
)


class Settings(BaseSettings):
    """API and worker settings. Every value comes from the environment or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://meeting:meeting@localhost:5442/meeting_agent"
    redis_url: str = "redis://localhost:6392/0"
    api_key: str = "dev-api-key"
    internal_token: str = "dev-internal-token"
    data_dir: Path = Path("data")

    elevenlabs_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Empty means the provider's own API. docker-compose.mock.yml points them at the mocks.
    elevenlabs_base_url: str = ""
    anthropic_base_url: str = ""
    openai_base_url: str = ""
    stt_model: str = "scribe_v2"
    summary_model: str = "claude-sonnet-5"
    embedding_model: str = "text-embedding-3-small"

    # The worker starts bot containers next to itself, so these name things on the Docker host.
    # The same image as the API and the worker; the bot is started with its own entrypoint.
    bot_image: str = "meeting-agent:latest"
    docker_network: str = "meeting-agent"
    data_volume: str = "meeting-agent-data"
    tailscale_container: str = "meeting-agent-tailscale"
    api_internal_url: str = "http://api:8000"

    default_egress_mode: EgressMode = EgressMode.DIRECT
    egress_proxy_url: str = ""
    bot_display_name: str = "Lexapar Notetaker (recording)"
    bot_account_label: str = ""
    notice_text: str = DEFAULT_NOTICE
    admission_timeout_s: int = 600
    alone_timeout_s: int = 30
    # Leaves when no meeting audio has arrived for this long, whatever the page shows.
    silence_timeout_s: int = 600
    max_meeting_s: int = 3 * 60 * 60


@cache
def get_settings() -> Settings:
    return Settings()


class BotSettings(BaseSettings):
    """What the worker passes to one bot container, as BOT_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="BOT_", extra="ignore")

    meeting_id: str
    platform: str
    join_url: str
    display_name: str
    egress_mode: EgressMode = EgressMode.DIRECT
    proxy_url: str = ""
    # A Chromium profile signed in by the bot-login container; empty for an anonymous guest.
    profile_dir: str = ""
    # <platform>.json here replaces an adapter's selectors without a rebuild.
    selectors_dir: Path = Path("/data/selectors")
    notice_text: str = DEFAULT_NOTICE
    notice_audio: str = ""
    admission_timeout_s: int = 600
    alone_timeout_s: int = 30
    # Leaves when no meeting audio has arrived for this long, whatever the page shows.
    silence_timeout_s: int = 600
    max_meeting_s: int = 3 * 60 * 60
    api_url: str = "http://api:8000"
    internal_token: str = ""
    out_dir: Path = Path("/data/meetings/unknown")


def meeting_dir(meeting_id: object) -> Path:
    """Where one meeting's audio, participants and debug files live, on the shared volume."""
    return get_settings().data_dir / "meetings" / str(meeting_id)
