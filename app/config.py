from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "medgemma"
    dhis2_url: str = ""
    dhis2_token: str = ""
    database_path: Path = Path("./data/screen_to_stage.db")
    image_dir: Path = Path("./data/images")


settings = Settings()
