from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TICKMARK_", env_file=".env")

    warden_url: str = "http://localhost:5000"
    docint_endpoint: str = ""
    docint_key: str = ""
    # The app connects as tickmark_app: SELECT + INSERT only, row-level security applies.
    database_url: str = "postgresql+psycopg://tickmark_app:tickmark_app@localhost:5435/tickmark"
    # The owner: migrations and provisioning tenants / engagements only.
    admin_database_url: str = "postgresql+psycopg://tickmark:tickmark@localhost:5435/tickmark"
    evidence_dir: str = "data/store"  # content-addressed copies of every file a run used


settings = Settings()
