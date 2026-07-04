import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    google_api_key: str
    elevenlabs_api_key: str
    service_account_file: str
    calendar_id_ali: str
    calendar_id_salem: str

    llm_model: str = "google:gemini-3.5-flash"
    elevenlabs_voice_id: str = (
        "cjVigY5qzO86Huf0OWal"  # Eric, premade (free tier allows premade only)
    )
    elevenlabs_tts_model: str = "eleven_flash_v2_5"
    # anchored to the project folder so it doesn't depend on where the server is started from
    db_path: str = str(PROJECT_ROOT / "sarjy.db")


settings = Settings()  # type: ignore[call-arg]  # required fields come from .env

# pydantic-ai's Google provider reads the key from the environment
os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
