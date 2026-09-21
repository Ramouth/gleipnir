"""Settings, loaded from environment / .env.

Deliberately small. Anything that varies per deployment lives here; anything
that varies per *analysis* (jurisdiction risk lists, designation dates,
predicate thresholds) is versioned reference data and does not belong in
config — see docs/predicates.md on why those must carry a version with them.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cvr_api_key: str = ""
    cvr_base_url: str = "http://distribution.virk.dk"
    raw_store_path: Path = Path("./raw")

    #: EPO Open Patent Services. OAuth2 client credentials from the EPO
    #: Developer Portal; the free tier is 4GB/week.
    epo_ops_key: str = ""
    epo_ops_secret: str = ""

    @property
    def cvr_configured(self) -> bool:
        return bool(self.cvr_api_key)

    @property
    def epo_configured(self) -> bool:
        return bool(self.epo_ops_key and self.epo_ops_secret)


settings = Settings()
