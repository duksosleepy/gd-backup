from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackupSettings(BaseSettings):
    """Application and backup settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.production"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(
        default="production",
        alias="ENVIRONMENT",
        description="Application environment mode (production / development / test)",
    )

    source_dir: Path = Field(
        default=Path("./data_to_backup"),
        alias="BACKUP_SOURCE_DIR",
        description="Local directory containing files to backup",
    )
    gdrive_folder_id: Optional[str] = Field(
        default=None,
        alias="GDRIVE_FOLDER_ID",
        description="Google Drive destination folder ID (None for root)",
    )
    credentials_file: Path = Field(
        default=Path("credentials.json"),
        alias="GDRIVE_CREDENTIALS_FILE",
        description="Path to client secrets JSON file",
    )
    token_file: Path = Field(
        default=Path("token.json"),
        alias="GDRIVE_TOKEN_FILE",
        description="Path to user OAuth token file",
    )
    schedule_hours: int = Field(
        default=24,
        alias="BACKUP_SCHEDULE_HOURS",
        description="Interval schedule in hours",
    )
    host: str = Field(
        default="0.0.0.0",
        alias="HOST",
        description="Web dashboard host address",
    )
    port: int = Field(
        default=8100,
        alias="PORT",
        description="Web dashboard port",
    )


class BackupInputParams(BaseModel):
    """Parameters exposed to user in the Web UI for manual backup runs."""

    source_dir: Optional[str] = Field(
        default=None,
        description="Optional override for local source directory",
    )
    gdrive_folder_id: Optional[str] = Field(
        default=None,
        description="Optional override for target Google Drive folder ID",
    )


settings = BackupSettings()
