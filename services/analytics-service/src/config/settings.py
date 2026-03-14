"""Application configuration."""

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="analytics-service")
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    app_role: str = Field(default="api")

    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    mysql_host: str = Field(default="mysql")
    mysql_port: int = Field(default=3306)
    mysql_database: str = Field(default="ai_factoryops")
    mysql_user: str = Field(default="energy")
    mysql_password: str = Field(default="energy")
    mysql_pool_size: int = Field(default=10)

    s3_bucket_name: str = Field(default="energy-platform-datasets")
    s3_region: str = Field(default="us-east-1")
    s3_endpoint_url: str | None = Field(default=None)
    s3_access_key_id: str | None = Field(default=None)
    s3_secret_access_key: str | None = Field(default=None)

    default_train_test_split: float = Field(default=0.8)
    max_dataset_size_mb: int = Field(default=500)
    supported_models: List[str] = Field(
        default=[
            "isolation_forest",
            "autoencoder",
            "random_forest",
            "gradient_boosting",
            "prophet",
            "arima",
        ]
    )

    max_concurrent_jobs: int = Field(default=3)
    job_timeout_seconds: int = Field(default=3600)
    job_lease_seconds: int = Field(default=60)
    job_heartbeat_seconds: int = Field(default=10)

    queue_backend: str = Field(default="redis")
    redis_url: str = Field(default="redis://redis:6379/0")
    redis_stream_name: str = Field(default="analytics_jobs_stream")
    redis_dead_letter_stream: str = Field(default="analytics_jobs_dead_letter")
    redis_consumer_group: str = Field(default="analytics_workers")
    redis_consumer_name: str = Field(default="analytics-worker-1")
    queue_max_attempts: int = Field(default=3)
    worker_heartbeat_ttl_seconds: int = Field(default=30)

    accuracy_min_labeled_events: int = Field(default=50)
    accuracy_certification_min_precision: float = Field(default=0.70)
    accuracy_certification_min_recall: float = Field(default=0.60)

    ml_analytics_v2_enabled: bool = Field(default=False)
    ml_formatted_results_enabled: bool = Field(default=True)
    ml_weekly_retrainer_enabled: bool = Field(default=True)
    ml_fleet_strict_enabled: bool = Field(default=True)
    ml_data_readiness_gate_enabled: bool = Field(default=False)
    ml_require_exact_dataset_range: bool = Field(default=True)

    data_export_service_url: str = Field(default="http://data-export-service:8080")
    data_service_url: str = Field(default="http://data-service:8081")
    data_readiness_poll_attempts: int = Field(default=3)
    data_readiness_initial_delay_seconds: int = Field(default=5)
    data_readiness_wait_timeout_seconds: int = Field(default=180)
    data_readiness_extended_wait_timeout_seconds: int = Field(default=480)
    data_readiness_max_concurrency: int = Field(default=3)
    data_readiness_export_cooldown_seconds: int = Field(default=30)
    data_readiness_trigger_retries: int = Field(default=3)
    data_readiness_status_retries: int = Field(default=2)
    data_service_query_timeout_seconds: int = Field(default=30)
    data_service_query_limit: int = Field(default=10000)
    data_service_fallback_chunk_hours: int = Field(default=6)

    @property
    def mysql_dsn(self) -> str:
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @property
    def mysql_sync_dsn(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
