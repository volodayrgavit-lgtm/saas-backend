from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "LAB51 Auth Core"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/v1"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # PostgreSQL
    DATABASE_URL: str = "postgresql+asyncpg://lab51:lab51_secret@localhost:5432/lab51_auth"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-me-to-a-random-secret-key-at-least-32-chars"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Argon2id
    ARGON2_TIME_COST: int = 3
    ARGON2_MEMORY_COST: int = 65536
    ARGON2_PARALLELISM: int = 4
    ARGON2_HASH_LEN: int = 32
    ARGON2_SALT_LEN: int = 16

    # OTP
    OTP_LENGTH: int = 6
    OTP_TTL_SECONDS: int = 600

    # Link Token
    LINK_TOKEN_TTL_SECONDS: int = 600

    # Rate Limiting
    RATE_LIMIT_LOGIN_PER_IP: int = 10
    RATE_LIMIT_LOGIN_WINDOW: int = 60
    RATE_LIMIT_REGISTER_PER_IP: int = 5
    RATE_LIMIT_REGISTER_WINDOW: int = 3600
    RATE_LIMIT_OTP_PER_IDENTITY: int = 3
    RATE_LIMIT_OTP_WINDOW: int = 600

    # Allowed email domains
    ALLOWED_EMAIL_DOMAINS: str = "mail.ru"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    @property
    def allowed_email_domains_list(self) -> List[str]:
        return [d.strip().lower() for d in self.ALLOWED_EMAIL_DOMAINS.split(",") if d.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()