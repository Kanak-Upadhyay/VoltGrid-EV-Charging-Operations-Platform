import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://voltgrid:chargeops@localhost:3307/voltgrid"
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_minutes: int = 720
    seed_on_startup: bool = False
    allow_meter_simulation: bool = True
    run_reconciler: bool = False
    reconcile_interval_seconds: int = 60
    stale_heartbeat_minutes: int = 5
    mysql_ssl: bool = False
    keyvault_url: str = ""
    jwt_secret_name: str = "jwt-secret"
    azure_storage_connection_string: str = ""
    azure_blob_container: str = "voltgrid-exports"
    appinsights_connection_string: str = ""


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        settings = Settings()
        if os.environ.get("VERCEL"):
            # Vercel has no local MySQL. /tmp is the writable disk on each instance.
            database_url = os.environ.get("DATABASE_URL", "")
            if not database_url or "localhost" in database_url or "127.0.0.1" in database_url:
                settings.database_url = "sqlite:////tmp/voltgrid.db"
            if not os.environ.get("SEED_ON_STARTUP"):
                settings.seed_on_startup = True
            if not os.environ.get("JWT_SECRET"):
                settings.jwt_secret = "voltgrid-vercel-demo-secret-key-32b"
            settings.run_reconciler = False
            settings.allow_meter_simulation = True
        _settings = settings
    return _settings


def apply_keyvault(settings: Settings) -> None:
    """Pull JWT secret from Azure Key Vault when KEYVAULT_URL is set.

    App Service can also inject the same value with a Key Vault reference
    and leave KEYVAULT_URL empty. This path is for managed-identity hosts.
    """
    if not settings.keyvault_url:
        return
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=settings.keyvault_url, credential=DefaultAzureCredential())
    settings.jwt_secret = client.get_secret(settings.jwt_secret_name).value
