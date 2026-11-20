from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Service identity (subclasses override)
    SERVICE_NAME: str        = "cyber-range-service"
    SERVICE_VERSION: str     = "0.1.0"
    HOST: str                = "0.0.0.0"
    PORT: int                = 8000
    DEBUG: bool              = False

    # Hostname the registry advertises — how *other services* reach us.
    # On a local VM this is the VM's IP. On OpenStack, the FIP or internal DNS name.
    PUBLIC_HOSTNAME: str     = "localhost"

    # TLS (the web edge sets these; empty = plain HTTP)
    # When both PEM files exist the service serves HTTPS, and registration
    # advertises an https health check + scrape scheme so Consul and
    # Prometheus talk TLS to it instead of failing the handshake.
    SSL_CERTFILE: str        = ""
    SSL_KEYFILE: str         = ""

    @property
    def tls_enabled(self) -> bool:
        return bool(
            self.SSL_CERTFILE and self.SSL_KEYFILE
            and Path(self.SSL_CERTFILE).is_file()
            and Path(self.SSL_KEYFILE).is_file()
        )

    # Service Registry (Consul)
    # Leave CONSUL_HOST empty to skip registration entirely (useful for pytest)
    CONSUL_HOST: str         = ""
    CONSUL_PORT: int         = 8500
    CONSUL_SCHEME: str       = "http"

    # Event Bus (RabbitMQ)
    # Empty string = disable, events will be logged locally instead
    RABBITMQ_URL: str        = ""
    EVENTS_EXCHANGE: str     = "cyber_range.events"

    # Optional external identity provider (JWKS), disabled by default
    AUTH_ENABLED: bool       = False
    KEYCLOAK_JWKS_URL: str   = ""
    KEYCLOAK_AUDIENCE: str   = "cyber-range"
    KEYCLOAK_ISSUER: str     = ""

    # Metrics (Prometheus)
    # When true, create_app() exposes a Prometheus /metrics endpoint with
    # default HTTP request/latency instrumentation. Prometheus discovers
    # the target via Consul and scrapes it. Set false to opt a service out.
    METRICS_ENABLED: bool    = True

    # Logging
    LOG_LEVEL: str           = "INFO"
    LOG_FORMAT: str          = Field("json", description="'json' for production, 'text' for local dev")

    # CORS (tighten per-service in production)
    CORS_ORIGINS: list[str]  = ["*"]

    # File-based secrets (SEC-021)
    # Docker Compose mounts secrets at /run/secrets/<name>. When a *_FILE
    # variable is set, the value is read from that file at start-up instead
    # of from the environment, so no key or password appears in the
    # deployment specification or in `docker inspect`.
    #
    # Token verification key (RS256 public key). Every service except IAM
    # holds only this public key, so a compromised service cannot mint tokens.
    JWT_PUBLIC_KEY_FILE: str      = ""
    # Event security (SR-FR-IL-34, SEC-020): each service signs the events it
    # publishes with its own Ed25519 key; consumers verify the signature
    # against the publisher's public key and decrypt the AES-256-GCM payload.
    EVENT_SECURITY: str           = "off"        # off | enforce
    EVENT_SIGNING_KEY_FILE: str   = ""
    EVENT_VERIFY_KEYS_DIR: str    = ""
    EVENT_ENCRYPTION_KEY_FILE: str = ""
    # Per-service database role (SEC-008): password injected into DATABASE_URL.
    DATABASE_USER: str            = ""
    DATABASE_PASSWORD_FILE: str   = ""
    # Per-service broker account (SEC-008): password injected into RABBITMQ_URL.
    RABBITMQ_PASSWORD_FILE: str   = ""

    @model_validator(mode="after")
    def _load_file_secrets(self):
        if self.JWT_PUBLIC_KEY_FILE and Path(self.JWT_PUBLIC_KEY_FILE).is_file() \
                and hasattr(self, "JWT_SECRET"):
            self.JWT_SECRET = Path(self.JWT_PUBLIC_KEY_FILE).read_text()
        if self.DATABASE_PASSWORD_FILE and Path(self.DATABASE_PASSWORD_FILE).is_file() \
                and hasattr(self, "DATABASE_URL"):
            pw = Path(self.DATABASE_PASSWORD_FILE).read_text().strip()
            url = str(getattr(self, "DATABASE_URL"))
            scheme, rest = url.split("://", 1)
            hostpart = rest.split("@", 1)[1] if "@" in rest else rest
            user = self.DATABASE_USER or (rest.split("@", 1)[0].split(":", 1)[0] if "@" in rest else "")
            self.DATABASE_URL = f"{scheme}://{quote(user, safe='')}:{quote(pw, safe='')}@{hostpart}"
        if self.RABBITMQ_PASSWORD_FILE and Path(self.RABBITMQ_PASSWORD_FILE).is_file() \
                and self.RABBITMQ_URL and "@" in self.RABBITMQ_URL:
            pw = Path(self.RABBITMQ_PASSWORD_FILE).read_text().strip()
            scheme, rest = self.RABBITMQ_URL.split("://", 1)
            cred, hostpart = rest.split("@", 1)
            self.RABBITMQ_URL = f"{scheme}://{cred.split(':', 1)[0]}:{quote(pw, safe='')}@{hostpart}"
        return self
