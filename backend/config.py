from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    database_url: str = "postgresql+psycopg://localhost/commerce"
    session_secret: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "Administrator"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_request_timeout_seconds: float = Field(default=45, ge=5, le=90)
    chat_timeout_seconds: float = Field(default=120, ge=30, le=180)
    store_url: str = "http://store.localhost:8080"
    gateway_url: str = "http://console.localhost:8080/mcp"
    internal_gateway_url: str = "http://127.0.0.1:8000/mcp"
    allowed_origins: str = "http://console.localhost:8080"
    allowed_internal_hosts: str = ""
    secret_backend: str = "kubernetes"
    secret_file: str = ".secrets/upstreams.json"
    upstream_credential_secret: str = "commerce-upstream-credentials"
    pod_namespace: str = "commerce"
    bootstrap_servers_json: str = "[]"
    tool_timeout_seconds: float = 15
    discovery_timeout_seconds: float = 8
    health_interval_seconds: float = 15
    session_hours: int = 12
    secure_cookies: bool = False
    testing: bool = False

    @cached_property
    def origins(self) -> set[str]:
        return {x.strip().rstrip("/") for x in self.allowed_origins.split(",") if x.strip()}

    @cached_property
    def internal_hosts(self) -> set[str]:
        return {x.strip().lower() for x in self.allowed_internal_hosts.split(",") if x.strip()}
