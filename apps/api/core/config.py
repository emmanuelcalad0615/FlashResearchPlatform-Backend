from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://flash:flash_dev_pw@localhost:5432/flash_research"
    redis_url: str = "redis://localhost:6379/0"
    debug: bool = False


settings = Settings()
