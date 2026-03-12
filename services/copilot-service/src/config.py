from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="copilot-service")
    app_version: str = Field(default="1.0.0")
    log_level: str = Field(default="INFO")

    ai_provider: str = Field(default="groq")
    groq_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")

    mysql_url: str = Field(
        default="mysql+aiomysql://copilot_reader:copilot_readonly_pass@mysql:3306/ai_factoryops"
    )
    data_service_url: str = Field(default="http://data-service:8081")
    reporting_service_url: str = Field(default="http://reporting-service:8085")
    factory_timezone: str = Field(default="Asia/Kolkata")

    max_query_rows: int = Field(default=200)
    query_timeout_sec: int = Field(default=10)
    max_history_turns: int = Field(default=5)

    stage1_max_tokens: int = Field(default=500)
    stage2_max_tokens: int = Field(default=900)


settings = Settings()
