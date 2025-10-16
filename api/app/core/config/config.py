from typing import Optional
from pydantic import PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # App
    APP_PORT: int = 8000

    # DB pieces (host was missing)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str

    # Optional full DSN; if unset we’ll build one from the pieces above
    DATABASE_URL: Optional[str] = None

    # Auth
    JWT_SECRET: SecretStr = SecretStr("fitesadev")
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_MIN: PositiveInt = 60
    REFRESH_TOKEN_DAYS: PositiveInt = 7

    # Files
    IMAGES_DIR: str = "images"

    # # pydantic-settings v2 config
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,   # read env vars case-insensitively
        # extra="ignore",         # ignore unexpected env vars
    )

    # # Convenience: synthesize DATABASE_URL if not provided
    # def effective_database_url(self) -> str:
    #     if self.DATABASE_URL:
    #         return self.DATABASE_URL
    #     return (
    #         f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
    #         f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    #     )

settings = Settings()
