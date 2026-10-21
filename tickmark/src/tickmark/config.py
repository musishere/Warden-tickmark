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
    # Reading PDFs: locally from their own text first; Azure only where that is not reliable.
    azure: str = "auto"  # auto: only where needed | off: never (those rows are 👀) | always: every PDF
    pdf_min_chars: int = 30  # a page with fewer text characters but an image is a scan
    pdf_image_cover: float = 0.7  # text over an image covering this share of the page is a scanner's OCR
    pdf_bad_chars: float = 0.02  # more unreadable characters than this share: the font has no text map
    pdf_rotated: float = 0.2  # more rotated characters than this share: a sideways scan or table
    pdf_word_gap: float = 15  # points between words that start a new block on the same line
    pdf_letterhead_share: float = 0.33  # the issuer's letterhead is never lower than this share of page 1
    evidence_dir: str = "data/store"  # content-addressed copies of every file a run used
    log_level: str = "INFO"  # INFO: each step's decision; DEBUG: every candidate and comparison too
    log_dir: str = "data/logs"  # tickmark.log, rotated (holds client values: local use)
    upload_dir: str = "data/uploads"  # files uploaded through the web app, one folder per upload


settings = Settings()
