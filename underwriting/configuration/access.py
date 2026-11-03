"""Configuration and fail-closed permission checks. Never log values."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv, dotenv_values
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

    service_mode: str = "offline"
    local_database_path: str = ".runtime/v2.sqlite"
    test_scope_confirmed: bool = False
    allowed_test_database_hosts: tuple = ()

    def missing(self, paid=True):
        pairs = [] if self.service_mode == "offline" else [("V2_TURSO_DATABASE_URL", self.database_url), ("V2_TURSO_AUTH_TOKEN", self.database_token)]
        if paid:
            pairs += [("V2_OPENAI_API_KEY", self.api_key), ("llm.model", self.model)]
        return [name for name, value in pairs if not value]


def load_settings(secrets=None):
    load_dotenv(ROOT / ".env", override=False)
    aliases = {
        "OPENAI_API_KEY": ("V2_OPENAI_API_KEY",),
        "TURSO_DATABASE_URL": ("V2_TURSO_DATABASE_URL",),
        "TURSO_AUTH_TOKEN": ("V2_TURSO_AUTH_TOKEN",),
    }
    def get(name):
        return next((os.environ.get(alias) or str((secrets or {}).get(alias, "")) for alias in aliases[name]
                     if os.environ.get(alias) or (secrets or {}).get(alias)), "")
    configuration = get_config()
    llm = configuration["llm"]
    if configuration['services']['mode']=='turso_readonly':
        # Explicit user-selected source, never an offline/test-mode fallback.
        values=dotenv_values(configuration['services']['credential_env_path'])
        def credential(*names):
            return next((values[name] for name in names if values.get(name)), '')
        return Settings('',llm['model'] or '',credential('V2_TURSO_DATABASE_URL','TURSO_DATABASE_URL'),
                        credential('V2_TURSO_AUTH_TOKEN','TURSO_AUTH_TOKEN'),'public_readonly',
                        llm['timeout_seconds'],dict(llm['generation']),'turso_readonly')
    return Settings(get("OPENAI_API_KEY"), llm["model"] or "", get("TURSO_DATABASE_URL"),
                    get("TURSO_AUTH_TOKEN"), configuration["access"]["mode"],
                    llm["timeout_seconds"], dict(llm["generation"]),
                    configuration["services"]["mode"], configuration["services"]["local_database_path"],
                    configuration["services"]["test_scope_confirmed"], tuple(configuration["services"]["allowed_test_database_hosts"]))


def local_server():
    import streamlit as st
    return st.get_option("server.address") in {"127.0.0.1", "::1"}


def require_owner(settings, proof=None):
    if settings.service_mode=='turso_readonly':
        raise PermissionError('Saved-results-only Turso connection: GPT requests, calculations and all database writes are disabled.')
    # Mode comes from server settings, never a client widget or session boolean.
    if settings.access_mode != "local_owner" or get_config()["access"]["mode"] != "local_owner" or not local_server():
        raise PermissionError("Writes and paid calls require local_owner mode on a loopback-bound server. public_readonly permits saved-result viewing only.")


def require_test_services(settings, *, generation=False):
    """Explicit test-only egress; legacy/global V1 credentials are never loaded."""
    if settings.service_mode == 'turso_readonly':
        if generation:
            raise PermissionError('Saved-results-only Turso connection: GPT generation is disabled. Load existing saved results instead.')
        return
    if settings.service_mode == 'offline':
        raise PermissionError('Offline mode: no external database or GPT request is allowed. Use local recorded fixtures, or explicitly configure separate V2 test services.')
    if settings.service_mode != 'test_turso' or not settings.test_scope_confirmed:
        raise PermissionError('Version 2 external services require confirmed test scope.')
    from urllib.parse import urlsplit
    host = urlsplit(settings.database_url.replace('libsql://','https://',1)).hostname
    if host not in settings.allowed_test_database_hosts:
        raise PermissionError('V2 test database hostname is not in services.allowed_test_database_hosts. No external connection was made.')
    if generation:
        require_owner(settings)
