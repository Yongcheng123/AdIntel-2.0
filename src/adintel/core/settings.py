from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[3]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_prefix="ADINTEL_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AdIntel"
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/adintel"
    )
    auto_apply_schema: bool = True
    state_dir: Path = ROOT_DIR / "state"
    config_file: Path = ROOT_DIR / "config" / "advertisers.yaml"
    socialpeta_group_config_file: Path = ROOT_DIR / "config" / "socialpeta_groups.yaml"
    cdp_url: str = "http://127.0.0.1:9222"
    browser_channel: str = "chromium"
    default_headless: bool = False
    collect_timeout_ms: int = 60_000
    sensortower_base_url: str = "https://app.sensortower.com"
    socialpeta_jitter_enabled: bool = True
    socialpeta_page_jitter_min_s: float = 0.6
    socialpeta_page_jitter_max_s: float = 1.6
    socialpeta_target_jitter_min_s: float = 1.2
    socialpeta_target_jitter_max_s: float = 3.0

    appfollow_base_url: str = "https://watch.appfollow.io"
    appfollow_workspace: str = ""  # set via ADINTEL_APPFOLLOW_WORKSPACE= in .env
    appfollow_group_config_file: Path = ROOT_DIR / "config" / "appfollow_groups.yaml"

    # Alerting
    alert_webhook_url: str | None = None
    alert_on_failure: bool = True
    alert_on_staleness: bool = True

    # Staleness thresholds
    stale_warning_hours: int = 48
    stale_critical_hours: int = 168
    max_consecutive_failures: int = 3

    @property
    def browser_state_dir(self) -> Path:
        return self.state_dir / "browser"

    @property
    def debug_dir(self) -> Path:
        return self.state_dir / "debug"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
