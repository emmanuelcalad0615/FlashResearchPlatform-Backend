from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://flash:flash_dev_pw@localhost:5432/flash_research"
    redis_url: str = "redis://localhost:6379/0"
    debug: bool = False

    # Logging
    # log_level: severidad minima que se emite (DEBUG/INFO/WARNING/ERROR/CRITICAL).
    # log_json:  true en produccion (una linea JSON por evento, indexable por
    #            herramientas); false en desarrollo (texto coloreado, legible).
    log_level: str = "INFO"
    log_json: bool = False

    # Proveedor de datos de mercado (Polygon.io)
    polygon_api_key: str = ""
    polygon_base_url: str = "https://api.polygon.io"


settings = Settings()
