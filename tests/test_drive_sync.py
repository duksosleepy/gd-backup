import hashlib
from pathlib import Path
from unittest.mock import MagicMock

from drive_sync import DriveSyncEngine, compute_file_md5


def test_compute_file_md5(tmp_path: Path):
    test_file = tmp_path / "sample.txt"
    content = b"Deterministic content for md5 testing"
    test_file.write_bytes(content)

    expected_md5 = hashlib.md5(content).hexdigest()
    assert compute_file_md5(test_file) == expected_md5


def test_sync_directory_new_files(mock_drive_service, sample_backup_dir: Path):
    engine = DriveSyncEngine(service=mock_drive_service)

    # Mock remote listing to return empty (all files are new)
    mock_files = mock_drive_service.files.return_value
    mock_files.list.return_value.execute.return_value = {"files": [], "nextPageToken": None}

    # Mock create returns an ID
    mock_files.create.return_value.execute.return_value = {"id": "new_created_file_id", "name": "sample"}
    mock_files.create.return_value.next_chunk.return_value = (None, {"id": "new_created_file_id", "name": "sample"})

    summary = engine.sync_directory(sample_backup_dir, target_folder_id="root_folder")

    assert summary.files_scanned == 3
    assert summary.files_uploaded == 3
    assert summary.files_updated == 0
    assert summary.files_skipped == 0
    assert len(summary.errors) == 0
    assert mock_files.create.call_count == 4  # 1 subfolder + 3 files


def test_sync_directory_skips_identical_files(mock_drive_service, sample_backup_dir: Path):
    engine = DriveSyncEngine(service=mock_drive_service)

    file1 = sample_backup_dir / "file1.txt"
    file1_md5 = compute_file_md5(file1)

    file2 = sample_backup_dir / "file2.json"
    file2_md5 = compute_file_md5(file2)

    # Mock remote listing to return existing files with matching MD5
    mock_files = mock_drive_service.files.return_value
    mock_files.list.return_value.execute.return_value = {
        "files": [
            {"id": "id1", "name": "file1.txt", "md5Checksum": file1_md5},
            {"id": "id2", "name": "file2.json", "md5Checksum": file2_md5},
            {"id": "id_sub", "name": "nested", "mimeType": "application/vnd.google-apps.folder"},
            {"id": "id3", "name": "file3.md", "md5Checksum": compute_file_md5(sample_backup_dir / "nested" / "file3.md")},
        ],
        "nextPageToken": None,
    }

    summary = engine.sync_directory(sample_backup_dir, target_folder_id="target_dir")

    assert summary.files_scanned == 3
    assert summary.files_uploaded == 0
    assert summary.files_updated == 0
    assert summary.files_skipped == 3
    assert mock_files.create.call_count == 0
    assert mock_files.update.call_count == 0


def test_sync_directory_updates_modified_files(mock_drive_service, sample_backup_dir: Path):
    engine = DriveSyncEngine(service=mock_drive_service)

    # Remote has file1 with an OLD / DIFFERENT md5 checksum
    mock_files = mock_drive_service.files.return_value
    mock_files.list.return_value.execute.return_value = {
        "files": [
            {"id": "id_file1", "name": "file1.txt", "md5Checksum": "different_old_md5_hash"},
            {"id": "id2", "name": "file2.json", "md5Checksum": compute_file_md5(sample_backup_dir / "file2.json")},
            {"id": "id_nested", "name": "nested", "mimeType": "application/vnd.google-apps.folder"},
            {"id": "id3", "name": "file3.md", "md5Checksum": compute_file_md5(sample_backup_dir / "nested" / "file3.md")},
        ],
        "nextPageToken": None,
    }
    mock_files.update.return_value.next_chunk.return_value = (None, {"id": "id_file1", "name": "file1.txt"})

    summary = engine.sync_directory(sample_backup_dir, target_folder_id="target_dir")

    assert summary.files_scanned == 3
    assert summary.files_updated == 1
    assert summary.files_skipped == 2
    assert summary.files_uploaded == 0
    assert mock_files.update.call_count == 1


def test_sync_directory_nonexistent_path(mock_drive_service, tmp_path: Path):
    engine = DriveSyncEngine(service=mock_drive_service)
    non_existent = tmp_path / "does_not_exist"

    summary = engine.sync_directory(non_existent)
    assert len(summary.errors) == 1
    assert "does not exist" in summary.errors[0]
