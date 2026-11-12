from pathlib import Path
import time
import pytest

from jobs import (
    JobConfig,
    JobsFileConfig,
    SourceFolderConfig,
    create_job_pipeline,
    find_latest_matching_file,
    load_all_jobs,
)


def test_source_folder_config_defaults():
    src = SourceFolderConfig(path=r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM_HRM")
    assert src.pattern == "*.zip"
    assert src.name == "L60SANGTAM_HRM"

    custom = SourceFolderConfig(
        path=r"E:\Custom\Path",
        pattern="*.bak",
        name="CustomDB",
    )
    assert custom.pattern == "*.bak"
    assert custom.name == "CustomDB"


def test_job_config_with_credentials_file():
    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        credentials_file="my_creds.json",
        token_file="my_token.json",
        target_folder="backup",
        schedule_hours=12,
        sources=[
            r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM_HRM",
            r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM",
        ],
    )
    assert job.id == "L60SANGTAM"
    assert job.credentials_file == "my_creds.json"
    assert job.token_file == "my_token.json"
    assert len(job.sources) == 2
    assert job.sources[0].name == "L60SANGTAM_HRM"
    assert job.sources[1].name == "L60SANGTAM"


def test_find_latest_matching_file(tmp_path):
    # Non-existent dir raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        find_latest_matching_file(tmp_path / "missing", "*.zip")

    # Empty dir raises FileNotFoundError
    test_dir = tmp_path / "backups"
    test_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        find_latest_matching_file(test_dir, "*.zip")

    # Create older zip
    file1 = test_dir / "backup_2026_01_01.zip"
    file1.write_text("old")

    time.sleep(0.05)

    # Create newer zip
    file2 = test_dir / "backup_2026_01_02.ZIP"
    file2.write_text("new")

    # Create other file type
    file3 = test_dir / "backup_2026_01_03.txt"
    file3.write_text("txt")

    latest = find_latest_matching_file(test_dir, "*.zip")
    assert latest.name == "backup_2026_01_02.ZIP"


def test_create_job_pipeline():
    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        credentials_file="credentials.json",
        token_file="token.json",
        target_folder="backup",
        schedule_hours=24,
        sources=[
            SourceFolderConfig(
                name="L60SANGTAM_HRM",
                path=r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM_HRM",
            ),
            SourceFolderConfig(
                name="L60SANGTAM",
                path=r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM",
            ),
        ],
    )
    pipeline = create_job_pipeline(job)
    assert pipeline.id == "L60SANGTAM"
    assert pipeline.name == "L60SANGTAM"
    assert len(pipeline.tasks) == 2
    assert pipeline.tasks[0].id == "l60sangtam_l60sangtam_hrm"
    assert pipeline.tasks[1].id == "l60sangtam_l60sangtam"

    # Verify task dependency
    assert pipeline.downstream_of(pipeline.tasks[0].id) == {pipeline.tasks[1].id}

    # Verify trigger
    assert len(pipeline.triggers) == 1
    assert pipeline.triggers[0].id == "scheduled_sync"


def test_create_job_pipeline_with_cron():
    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        credentials_file="credentials.json",
        token_file="token.json",
        target_folder="backup",
        cron="0 5 * * *",
        sources=[
            SourceFolderConfig(
                name="L60SANGTAM_HRM",
                path=r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM_HRM",
            ),
            SourceFolderConfig(
                name="L60SANGTAM",
                path=r"E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM",
            ),
        ],
    )
    pipeline = create_job_pipeline(job)
    assert pipeline.id == "L60SANGTAM"
    assert len(pipeline.triggers) == 1
    assert pipeline.triggers[0].id == "cron_trigger"
    assert "Daily at 5:00 AM" in pipeline.triggers[0].name


def test_load_all_jobs_from_jobs_yaml():
    jobs = load_all_jobs(Path("jobs.yaml"))
    assert len(jobs) >= 1
    l60 = next((j for j in jobs if j.id == "L60SANGTAM"), None)
    assert l60 is not None
    assert l60.name == "L60SANGTAM"
    assert l60.cron == "0 5 * * *"
    assert l60.credentials_file == "credentials.json"
    assert len(l60.sources) == 2


def test_current_month_folder_name_utc7():
    from datetime import datetime, timezone
    from jobs import current_month_folder_name

    # 2026-10-31 20:00 UTC is already 2026-11-01 03:00 in UTC+7
    now = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    assert current_month_folder_name("%Y-%m", now) == "2026-11"


def test_month_folder_reused_if_exists():
    from unittest.mock import MagicMock
    from drive_sync import DriveSyncEngine

    service = MagicMock()
    service.files().list().execute.return_value = {"files": [{"id": "M1", "name": "2026-11"}]}
    engine = DriveSyncEngine(service=service)
    assert engine.get_or_create_subfolder("2026-11", "PARENT") == "M1"
    service.files().create.assert_not_called()
