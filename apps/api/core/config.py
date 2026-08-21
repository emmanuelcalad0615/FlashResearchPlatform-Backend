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

    # Rate limiting (ventana fija por IP, contador en Redis)
    # rate_limit_enabled: se apaga en los tests, que no tocan la red.
    # /health exento: el healthcheck de Docker lo llama cada 5s y se
    # autobloquearia, haciendo que Docker reiniciara la API en bucle.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    rate_limit_exempt_paths: Annotated[list[str], NoDecode] = ["/health"]

    # Proveedor de datos de mercado (Polygon.io)
    polygon_api_key: str = ""
    polygon_base_url: str = "https://api.polygon.io"

    @field_validator("cors_origins", "rate_limit_exempt_paths", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Acepta 'a,b' ademas de una lista."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


settings = Settings()
