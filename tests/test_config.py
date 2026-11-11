from pathlib import Path
from config import BackupInputParams, BackupSettings


def test_backup_settings_defaults():
    settings = BackupSettings()
    assert settings.host == "0.0.0.0"
    assert settings.port == 8100
    assert settings.schedule_hours == 24
    assert isinstance(settings.source_dir, Path)
    assert settings.credentials_file == Path("credentials.json")
    assert settings.token_file == Path("token.json")


def test_backup_settings_overrides():
    settings = BackupSettings(
        BACKUP_SOURCE_DIR="/tmp/custom_data",
        GDRIVE_FOLDER_ID="custom_drive_folder_id",
        BACKUP_SCHEDULE_HOURS=6,
        PORT=9000,
    )
    assert settings.source_dir == Path("/tmp/custom_data")
    assert settings.gdrive_folder_id == "custom_drive_folder_id"
    assert settings.schedule_hours == 6
    assert settings.port == 9000


def test_backup_input_params():
    # Empty params
    params = BackupInputParams()
    assert params.source_dir is None
    assert params.gdrive_folder_id is None

    # Custom override params
    custom_params = BackupInputParams(
        source_dir="/custom/path",
        gdrive_folder_id="folder_abc",
    )
    assert custom_params.source_dir == "/custom/path"
    assert custom_params.gdrive_folder_id == "folder_abc"
