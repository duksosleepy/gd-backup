from pathlib import Path
from unittest.mock import MagicMock
import pytest

from config import BackupSettings


@pytest.fixture
def mock_drive_service():
    """Create a mock Google Drive v3 Resource service."""
    service = MagicMock()
    return service


@pytest.fixture
def sample_backup_dir(tmp_path: Path):
    """Create a temporary directory structure with sample files for backup testing."""
    source_dir = tmp_path / "test_data"
    source_dir.mkdir()

    # Create root level files
    (source_dir / "file1.txt").write_text("Hello world", encoding="utf-8")
    (source_dir / "file2.json").write_text('{"key": "value"}', encoding="utf-8")

    # Create nested folder and file
    sub_dir = source_dir / "nested"
    sub_dir.mkdir()
    (sub_dir / "file3.md").write_text("# Markdown file in nested dir", encoding="utf-8")

    return source_dir


@pytest.fixture
def test_settings(tmp_path: Path, sample_backup_dir: Path):
    """Create a BackupSettings instance configured for isolated testing."""
    return BackupSettings(
        BACKUP_SOURCE_DIR=sample_backup_dir,
        GDRIVE_FOLDER_ID="mock_folder_123",
        GDRIVE_CREDENTIALS_FILE=tmp_path / "credentials.json",
        GDRIVE_TOKEN_FILE=tmp_path / "token.json",
        BACKUP_SCHEDULE_HOURS=12,
        HOST="127.0.0.1",
        PORT=8080,
    )
