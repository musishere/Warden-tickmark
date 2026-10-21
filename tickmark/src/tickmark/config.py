from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TICKMARK_", env_file=".env")

    warden_url: str = "http://localhost:5000"
    docint_endpoint: str = ""
    docint_key: str = ""
    docint_model: str = "prebuilt-invoice"  # Azure Document Intelligence model that reads the invoices
    # The app connects as tickmark_app: SELECT + INSERT only, row-level security applies.
    database_url: str = "postgresql+psycopg://tickmark_app:tickmark_app@localhost:5435/tickmark"
    # The owner: migrations and provisioning tenants / engagements only.
    admin_database_url: str = "postgresql+psycopg://tickmark:tickmark@localhost:5435/tickmark"
    # LLM-assisted column mapping. Off by default: client data stays in the building.
    llm_mapping: bool = False  # send headers + value PATTERNS (no values) to the LLM
    llm_mapping_values: bool = False  # separately: also send up to 5 real example values per column
    # Any OpenAI-compatible chat completions API (OpenRouter by default in .env.example).
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_mapping_model: str = ""
    llm_timeout_seconds: float = 120
    header_scan_rows: int = 20  # the header row is looked for in this many non-blank rows at the top
    evidence_dir: str = "data/store"  # content-addressed copies of every file a run used


settings = Settings()
