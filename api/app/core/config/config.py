from typing import Optional
from pydantic import PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_PORT: int = 8000

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str

    DATABASE_URL: Optional[str] = None

    JWT_SECRET: str = 'fitesa-dev'
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_MIN: PositiveInt = 60
    REFRESH_TOKEN_DAYS: PositiveInt = 7

    IMAGES_DIR: str = "images"
    
    #  rtsp_url = f'rtsp://{username}:{password}@{base_url}{path_template.format(channel=channel)}'
    HLS_ROOT: str = "./hls"
    CAMERA_RTSP_USERNAME: str = "user"
    CAMERA_RTSP_PASSWORD: str = "password"
    CAMERA_RTSP_PATH: str = "/cam/realmonitor?subtype=0&channel="
    HLS_PUBLIC_BASE: str = "http://localhost:8000/hls"
    

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        # extra="ignore",         # ignore unexpected env vars
    )



settings = Settings()
