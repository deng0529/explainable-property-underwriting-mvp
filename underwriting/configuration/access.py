"""Configuration and fail-closed permission checks. Never log values."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from underwriting.configuration.settings import get_config

from underwriting.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    model: str = ""
    database_url: str = field(default="", repr=False)
    database_token: str = field(default="", repr=False)
    access_mode: str = "public_readonly"
    timeout_seconds: float = 120.0
    generation: dict = field(default_factory=dict)

    def missing(self, paid=True):
        pairs = [("TURSO_DATABASE_URL", self.database_url), ("TURSO_AUTH_TOKEN", self.database_token)]
        if paid:
            pairs += [("OPENAI_API_KEY", self.api_key), ("llm.model", self.model)]
        return [name for name, value in pairs if not value]


def load_settings(secrets=None):
    load_dotenv(ROOT / ".env", override=False)
    aliases = {
        "OPENAI_API_KEY": ("OPENAI_API_KEY", "OPENAI_KEY", "OPENAI_API_TOKEN"),
        "TURSO_DATABASE_URL": ("TURSO_DATABASE_URL", "TURSO_URL", "LIBSQL_URL", "DATABASE_URL"),
        "TURSO_AUTH_TOKEN": ("TURSO_AUTH_TOKEN", "TURSO_TOKEN", "LIBSQL_AUTH_TOKEN", "DATABASE_AUTH_TOKEN"),
    }
    def get(name):
        return next((os.environ.get(alias) or str((secrets or {}).get(alias, "")) for alias in aliases[name]
                     if os.environ.get(alias) or (secrets or {}).get(alias)), "")
    configuration = get_config()
    llm = configuration["llm"]
    return Settings(get("OPENAI_API_KEY"), llm["model"] or "", get("TURSO_DATABASE_URL"),
                    get("TURSO_AUTH_TOKEN"), configuration["access"]["mode"],
                    llm["timeout_seconds"], dict(llm["generation"]))


def local_server():
    import streamlit as st
    return st.get_option("server.address") in {"127.0.0.1", "::1"}


def require_owner(settings, proof=None):
    # Mode comes from server settings, never a client widget or session boolean.
    if settings.access_mode != "local_owner" or get_config()["access"]["mode"] != "local_owner" or not local_server():
        raise PermissionError("Writes and paid calls require local_owner mode on a loopback-bound server. public_readonly permits saved-result viewing only.")
