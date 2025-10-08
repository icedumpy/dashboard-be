from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_PORT: int
    
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_PORT: int
    DATABASE_URL: str
    
    
    JWT_SECRET: str = "fitesadev"
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_MIN: int = 60
    REFRESH_TOKEN_DAYS: int = 7
    IMAGES_DIR: str = 'images'

    class Config:
        env_file = ".env"

settings = Settings()
