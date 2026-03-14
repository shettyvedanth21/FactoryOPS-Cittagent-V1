from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    DATABASE_URL: str = "mysql+aiomysql://energy:energy@mysql:3306/ai_factoryops"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 3600

    INFLUXDB_URL: str = "http://influxdb:8086"
    INFLUXDB_TOKEN: str = "energy-token"
    INFLUXDB_ORG: str = "energy-org"
    INFLUXDB_BUCKET: str = "telemetry"
    INFLUXDB_MEASUREMENT: str = "device_telemetry"
    INFLUX_AGGREGATION_WINDOW: str = "5m"
    INFLUX_MAX_POINTS: int = 10000

    DEVICE_SERVICE_URL: str = "http://device-service:8000"
    REPORTING_SERVICE_URL: str = "http://reporting-service:8085"

    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_EXTERNAL_URL: str = "http://localhost:9000"
    MINIO_ACCESS_KEY: str = "minio"
    MINIO_SECRET_KEY: str = "minio123"
    MINIO_BUCKET: str = "factoryops-waste-reports"
    MINIO_SECURE: bool = False

    TARIFF_CACHE_TTL_SECONDS: int = 60
    WASTE_STRICT_QUALITY_GATE: bool = False
    WASTE_JOB_TIMEOUT_SECONDS: int = 600
    WASTE_DEVICE_CONCURRENCY: int = 16
    WASTE_DB_BATCH_SIZE: int = 500
    WASTE_PDF_MAX_DEVICES: int = 200


settings = Settings()
