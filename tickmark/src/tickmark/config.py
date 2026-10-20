from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TICKMARK_", env_file=".env")

    warden_url: str = "http://localhost:5000"
    docint_endpoint: str = ""
    docint_key: str = ""
    database_url: str = "postgresql+psycopg://tickmark:tickmark@localhost:5435/tickmark"
    evidence_dir: str = "data/store"  # content-addressed copies of every file a run used


settings = Settings()
