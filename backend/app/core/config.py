import os

from pydantic import BaseModel, ConfigDict, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    telegram_bot_token: SecretStr
    telegram_chat_id: str
    google_geolocation_api_key: SecretStr

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            telegram_bot_token=SecretStr(os.getenv("TELEGRAM_BOT_TOKEN", "")),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            google_geolocation_api_key=SecretStr(
                os.getenv("GOOGLE_GEOLOCATION_API_KEY", "")
            ),
        )


settings = Settings.from_environment()
