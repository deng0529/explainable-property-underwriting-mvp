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
        prefix = "" if self.service_mode in {"turso_readonly","comparison_owner"} else "V2_"
        pairs = [] if self.service_mode == "offline" else [(prefix+"TURSO_DATABASE_URL", self.database_url), (prefix+"TURSO_AUTH_TOKEN", self.database_token)]
        if paid and self.service_mode != "turso_readonly":
            pairs += [(prefix+"OPENAI_API_KEY", self.api_key), ("llm.model", self.model)]
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
        # Public Cloud runs never open a developer's optional Windows path.
        # Only database credentials are selected; GPT remains disabled.
        def supplied(*names):
            return next((str(source[name]) for source in (secrets or {},os.environ)
                         for name in names if source.get(name)), '')
        names=(('V2_TURSO_DATABASE_URL','TURSO_DATABASE_URL'),('V2_TURSO_AUTH_TOKEN','TURSO_AUTH_TOKEN'))
        values={}
        local_path=configuration['services'].get('credential_env_path')
        if local_path and os.environ.get('UNDERWRITING_PROFILE')!='public_readonly' and not all(supplied(*pair) for pair in names):
            values=dotenv_values(local_path)
        def credential(*names):
            return supplied(*names) or next((values[name] for name in names if values.get(name)), '')
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


def comparison_available(settings):
    config=get_config()
    return (config['belief_comparison']['enabled'] and config['belief_comparison']['local_owner_enabled']
            and config['access']['mode']=='local_owner' and local_server()
            and os.environ.get('UNDERWRITING_PROFILE','')!='public_readonly')


def local_comparison_settings(settings):
    """Explicit local-only proposal experiment. Never grant access to public clients."""
    if not comparison_available(settings):
        return settings
    if settings.service_mode!='turso_readonly':
        return settings  # Existing offline/test owner controls still apply.
    path=get_config()['services'].get('credential_env_path')
    values=dotenv_values(path) if path else {}
    def value(*names):
        return next((source[name] for source in (os.environ,values) for name in names if source.get(name)),'')
    return Settings(value('V2_OPENAI_API_KEY','OPENAI_API_KEY'),settings.model,
        settings.database_url,settings.database_token,'local_owner',settings.timeout_seconds,
        dict(settings.generation),'comparison_owner')


def require_owner(settings, proof=None):
    if settings.service_mode=="comparison_owner" and not comparison_available(settings):
        raise PermissionError("The proposal comparison experiment requires an explicitly enabled local owner on loopback.")
    if settings.service_mode=='turso_readonly':
        raise PermissionError('Saved-results-only Turso connection: GPT requests, calculations and all database writes are disabled.')
    # Mode comes from server settings, never a client widget or session boolean.
    if settings.access_mode != "local_owner" or get_config()["access"]["mode"] != "local_owner" or not local_server():
        raise PermissionError("Writes and paid calls require local_owner mode on a loopback-bound server. public_readonly permits saved-result viewing only.")


def require_test_services(settings, *, generation=False):
    """Explicit test-only egress; legacy/global V1 credentials are never loaded."""
    if settings.service_mode == 'comparison_owner':
        require_owner(settings)
        return
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
