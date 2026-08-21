from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # CORS
    # Origenes exactos que el navegador puede usar para llamar a esta API.
    # NUNCA "*": con allow_credentials el navegador lo rechaza, y ademas
    # abriria la API a cualquier sitio web.
    # NoDecode desactiva el parseo JSON automatico para poder aceptar una lista
    # separada por comas, que es lo comodo en un .env.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Proveedor de datos de mercado (Polygon.io)
    polygon_api_key: str = ""
    polygon_base_url: str = "https://api.polygon.io"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Acepta 'http://a,http://b' ademas de una lista."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
