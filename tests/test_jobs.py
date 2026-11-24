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


def test_load_all_jobs_from_jobs_yml_root():
    jobs = load_all_jobs(Path("jobs.yml"))
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
    assert current_month_folder_name("%m", now) == "11"


def test_current_day_folder_name_utc7():
    from datetime import datetime, timezone
    from jobs import current_day_folder_name

    # 2026-11-19 20:00 UTC is 2026-11-20 03:00 in UTC+7
    now = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)
    assert current_day_folder_name("%d", now) == "20"


def test_month_and_day_folder_hierarchy():
    from unittest.mock import MagicMock
    from drive_sync import DriveSyncEngine

    service = MagicMock()
    # Mocking: parent -> month (11) -> day (20)
    service.files().list().execute.side_effect = [
        {"files": [{"id": "MONTH_ID", "name": "11"}]},
        {"files": [{"id": "DAY_ID", "name": "20"}]},
    ]
    engine = DriveSyncEngine(service=service)

    month_id = engine.get_or_create_subfolder("11", "PARENT_ID")
    assert month_id == "MONTH_ID"

    day_id = engine.get_or_create_subfolder("20", month_id)
    assert day_id == "DAY_ID"


def test_job_run_params_fields():
    from jobs import JobRunParams

    fields = JobRunParams.model_fields
    assert "gdrive_parent_folder_id" in fields
    assert "credentials_file" in fields
    assert "target_folder" not in fields
    assert "dry_run" not in fields
    assert fields["gdrive_parent_folder_id"].title == "Folder parent id"
    assert fields["credentials_file"].title == "Credential file"


def test_job_config_parent_id_support():
    # Test setting parent_id
    job1 = JobConfig(
        id="TEST_PARENT",
        name="Test Parent",
        parent_id="DRIVE_FOLDER_123",
        sources=[r"E:\Test"],
    )
    assert job1.parent_id == "DRIVE_FOLDER_123"
    assert job1.gdrive_parent_folder_id == "DRIVE_FOLDER_123"

    # Test setting gdrive_parent_folder_id
    job2 = JobConfig(
        id="TEST_PARENT_2",
        name="Test Parent 2",
        gdrive_parent_folder_id="DRIVE_FOLDER_456",
        sources=[r"E:\Test"],
    )
    assert job2.parent_id == "DRIVE_FOLDER_456"
    assert job2.gdrive_parent_folder_id == "DRIVE_FOLDER_456"


def test_make_job_run_params_class_defaults():
    from jobs import make_job_run_params_class

    job = JobConfig(
        id="JOB_DEFAULT_TEST",
        name="Job Default Test",
        parent_id="PRESET_FOLDER_ID",
        credentials_file="custom_creds.json",
        sources=[r"E:\Test"],
    )
    param_cls = make_job_run_params_class(job)
    assert param_cls.model_fields["gdrive_parent_folder_id"].default == "PRESET_FOLDER_ID"
    assert param_cls.model_fields["credentials_file"].default == "custom_creds.json"

    # Default instantiation without args should have the preset folder ID
    instance = param_cls()
    assert instance.gdrive_parent_folder_id == "PRESET_FOLDER_ID"


def test_update_job_parent_id_in_file(tmp_path):
    from jobs import update_job_parent_id_in_file

    yaml_content = """# Comments preserved
jobs:
  - id: "L60SANGTAM"
    name: "L60SANGTAM"
    credentials_file: "credentials.json"
    cron: "0 5 * * *" # Daily at 5:00 AM
    enabled: true
"""
    yaml_file = tmp_path / "jobs.yml"
    yaml_file.write_text(yaml_content, encoding="utf-8")

    # Insert parent_id
    update_job_parent_id_in_file("L60SANGTAM", "FOLDER_AAA", config_path=yaml_file)
    text1 = yaml_file.read_text(encoding="utf-8")
    assert 'parent_id: "FOLDER_AAA"' in text1
    assert "# Daily at 5:00 AM" in text1

    # Update existing parent_id
    update_job_parent_id_in_file("L60SANGTAM", "FOLDER_BBB", config_path=yaml_file)
    text2 = yaml_file.read_text(encoding="utf-8")
    assert 'parent_id: "FOLDER_BBB"' in text2
    assert 'parent_id: "FOLDER_AAA"' not in text2
    assert "# Daily at 5:00 AM" in text2


def test_automated_and_manual_run_parent_id_consistency(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import drive_sync
    from jobs import JobRunParams, make_source_backup_task

    # Create dummy source directory and file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "backup_2026.zip").write_text("content")

    # Create dummy jobs.yml
    config_file = tmp_path / "jobs.yml"
    config_file.write_text(
        f"""jobs:
  - id: "L60SANGTAM"
    name: "L60SANGTAM"
    parent_id: "FOLDER_CONSISTENT_123"
    sources:
      - path: "{str(src_dir)}"
        pattern: "*.zip"
""",
        encoding="utf-8",
    )

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {"files": []}
    mock_engine = MagicMock()
    # Month/day subfolder mock returns subfolder ID
    mock_engine.get_or_create_subfolder.side_effect = lambda folder_name, parent_id: f"{parent_id}/{folder_name}"
    mock_engine.list_remote_files.return_value = {}
    mock_engine.sync_file.return_value = {"status": "uploaded"}

    monkeypatch.setattr("jobs.get_drive_service", lambda *args, **kwargs: mock_service)
    monkeypatch.setattr("jobs.DriveSyncEngine", lambda *args, **kwargs: mock_engine)
    monkeypatch.setattr("jobs.get_active_jobs_file", lambda config_path=None: config_file)

    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        parent_id="FOLDER_CONSISTENT_123",
        sources=[SourceFolderConfig(path=str(src_dir), pattern="*.zip")],
    )
    src_config = job.sources[0]
    task = make_source_backup_task(job, src_config)

    # 1. Automated run (params is None or empty dict validation)
    res_auto = task.run(params=None)
    # The base destination folder passed into subfolders was FOLDER_CONSISTENT_123
    first_call_parent_id = mock_engine.get_or_create_subfolder.call_args_list[0][1]["parent_id"]
    assert first_call_parent_id == "FOLDER_CONSISTENT_123"

    mock_engine.get_or_create_subfolder.reset_mock()

    # 2. Manual run with explicit new parent_id override
    manual_params = JobRunParams(gdrive_parent_folder_id="NEW_MANUAL_FOLDER_789")
    res_manual = task.run(params=manual_params)
    first_call_manual = mock_engine.get_or_create_subfolder.call_args_list[0][1]["parent_id"]
    assert first_call_manual == "NEW_MANUAL_FOLDER_789"

    # Verify that the manual run persisted this to jobs.yml
    saved_yaml = config_file.read_text(encoding="utf-8")
    assert 'parent_id: "NEW_MANUAL_FOLDER_789"' in saved_yaml

    mock_engine.get_or_create_subfolder.reset_mock()

    # 3. Subsequent automated run reads the newly persisted value from jobs.yml
    res_subsequent_auto = task.run(params=None)
    first_call_subsequent = mock_engine.get_or_create_subfolder.call_args_list[0][1]["parent_id"]
    assert first_call_subsequent == "NEW_MANUAL_FOLDER_789"


def test_load_all_jobs_from_jobs_yml(tmp_path):
    yml_file = tmp_path / "jobs.yml"
    yml_file.write_text(
        """jobs:
  - id: "YML_JOB"
    name: "YML Job"
    cron: "0 3 * * *"
    parent_id: "PID_123"
    sources:
      - path: 'E:\\Test'
""",
        encoding="utf-8",
    )
    jobs = load_all_jobs(yml_file)
    assert len(jobs) == 1
    assert jobs[0].id == "YML_JOB"
    assert jobs[0].cron == "0 3 * * *"
    assert jobs[0].parent_id == "PID_123"


def test_job_scheduler_registration_and_trigger_active():
    from core import register_pipeline, orchestrator

    job = JobConfig(
        id="SCHED_TEST_JOB",
        name="Scheduler Test Job",
        cron="0 5 * * *",
        parent_id="PID_TEST",
        sources=[r"E:\Test"],
    )
    pipeline = create_job_pipeline(job)
    assert len(pipeline.triggers) == 1
    assert pipeline.triggers[0].id == "cron_trigger"

    registered = register_pipeline(pipeline)
    assert registered.id == "SCHED_TEST_JOB"

    job_entry = orchestrator.get_job("SCHED_TEST_JOB", "cron_trigger")
    assert job_entry is not None
    assert job_entry.id == "SCHED_TEST_JOB: cron_trigger"


def test_month_day_folder_skip_when_two_files_exist(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from jobs import JobConfig, SourceFolderConfig, make_source_backup_task

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "db_backup_20261120.zip").write_text("dummy backup")

    mock_service = MagicMock()
    mock_engine = MagicMock()
    mock_engine.get_or_create_subfolder.side_effect = lambda folder_name, parent_id: f"{parent_id}/{folder_name}"

    # 2 files already in month/day folder
    mock_engine.list_remote_files.return_value = {
        "file1.zip": {"id": "1", "name": "file1.zip", "mimeType": "application/zip"},
        "file2.zip": {"id": "2", "name": "file2.zip", "mimeType": "application/zip"},
    }

    monkeypatch.setattr("jobs.get_drive_service", lambda *args, **kwargs: mock_service)
    monkeypatch.setattr("jobs.DriveSyncEngine", lambda *args, **kwargs: mock_engine)

    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        parent_id="ROOT_PID",
        sources=[SourceFolderConfig(path=str(src_dir), pattern="*.zip")],
    )
    task = make_source_backup_task(job, job.sources[0])

    res = task.run()
    assert res["status"] == "skipped"
    assert "already contains 2 files" in res["reason"]
    mock_engine.sync_file.assert_not_called()


def test_month_day_folder_skip_when_file_already_in_folder(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from jobs import JobConfig, SourceFolderConfig, make_source_backup_task

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "db_backup_20261120.zip").write_text("dummy backup")

    mock_service = MagicMock()
    mock_engine = MagicMock()
    mock_engine.get_or_create_subfolder.side_effect = lambda folder_name, parent_id: f"{parent_id}/{folder_name}"

    # Only 1 file in folder (< 2), but it is the exact same file
    mock_engine.list_remote_files.return_value = {
        "db_backup_20261120.zip": {
            "id": "1",
            "name": "db_backup_20261120.zip",
            "mimeType": "application/zip",
        },
    }

    monkeypatch.setattr("jobs.get_drive_service", lambda *args, **kwargs: mock_service)
    monkeypatch.setattr("jobs.DriveSyncEngine", lambda *args, **kwargs: mock_engine)

    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        parent_id="ROOT_PID",
        sources=[SourceFolderConfig(path=str(src_dir), pattern="*.zip")],
    )
    task = make_source_backup_task(job, job.sources[0])

    res = task.run()
    assert res["status"] == "skipped"
    assert "already exists in destination folder" in res["reason"]
    mock_engine.sync_file.assert_not_called()


def test_month_day_folder_uploads_when_less_than_two_files_and_new_file(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from jobs import JobConfig, SourceFolderConfig, make_source_backup_task

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "db_backup_20261120.zip").write_text("dummy backup")

    mock_service = MagicMock()
    mock_engine = MagicMock()
    mock_engine.get_or_create_subfolder.side_effect = lambda folder_name, parent_id: f"{parent_id}/{folder_name}"

    # 1 file in folder from different source, local file is new
    mock_engine.list_remote_files.return_value = {
        "hrm_backup_20261120.zip": {
            "id": "1",
            "name": "hrm_backup_20261120.zip",
            "mimeType": "application/zip",
        },
    }

    def mock_sync(local_file, parent_folder_id, remote_files, summary):
        summary.files_uploaded += 1

    mock_engine.sync_file.side_effect = mock_sync

    monkeypatch.setattr("jobs.get_drive_service", lambda *args, **kwargs: mock_service)
    monkeypatch.setattr("jobs.DriveSyncEngine", lambda *args, **kwargs: mock_engine)

    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        parent_id="ROOT_PID",
        sources=[SourceFolderConfig(path=str(src_dir), pattern="*.zip")],
    )
    task = make_source_backup_task(job, job.sources[0])

    res = task.run()
    assert res["status"] == "uploaded"
    mock_engine.sync_file.assert_called_once()


def test_month_day_folder_subfolders_ignored_in_file_count(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from jobs import JobConfig, SourceFolderConfig, make_source_backup_task

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "db_backup_20261120.zip").write_text("dummy backup")

    mock_service = MagicMock()
    mock_engine = MagicMock()
    mock_engine.get_or_create_subfolder.side_effect = lambda folder_name, parent_id: f"{parent_id}/{folder_name}"

    # 2 items in folder, but both are Google Drive folders (not files!)
    mock_engine.list_remote_files.return_value = {
        "subfolder1": {"id": "f1", "name": "subfolder1", "mimeType": "application/vnd.google-apps.folder"},
        "subfolder2": {"id": "f2", "name": "subfolder2", "mimeType": "application/vnd.google-apps.folder"},
    }

    def mock_sync(local_file, parent_folder_id, remote_files, summary):
        summary.files_uploaded += 1

    mock_engine.sync_file.side_effect = mock_sync

    monkeypatch.setattr("jobs.get_drive_service", lambda *args, **kwargs: mock_service)
    monkeypatch.setattr("jobs.DriveSyncEngine", lambda *args, **kwargs: mock_engine)

    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        parent_id="ROOT_PID",
        sources=[SourceFolderConfig(path=str(src_dir), pattern="*.zip")],
    )
    task = make_source_backup_task(job, job.sources[0])

    res = task.run()
    assert res["status"] == "uploaded"
    mock_engine.sync_file.assert_called_once()


def test_month_day_folder_force_upload_bypasses_check(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from jobs import JobConfig, SourceFolderConfig, make_source_backup_task, JobRunParams

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "db_backup_20261120.zip").write_text("dummy backup")

    mock_service = MagicMock()
    mock_engine = MagicMock()
    mock_engine.get_or_create_subfolder.side_effect = lambda folder_name, parent_id: f"{parent_id}/{folder_name}"

    # 2 files already in month/day folder
    mock_engine.list_remote_files.return_value = {
        "file1.zip": {"id": "1", "name": "file1.zip", "mimeType": "application/zip"},
        "file2.zip": {"id": "2", "name": "file2.zip", "mimeType": "application/zip"},
    }

    def mock_sync(local_file, parent_folder_id, remote_files, summary):
        summary.files_uploaded += 1

    mock_engine.sync_file.side_effect = mock_sync

    monkeypatch.setattr("jobs.get_drive_service", lambda *args, **kwargs: mock_service)
    monkeypatch.setattr("jobs.DriveSyncEngine", lambda *args, **kwargs: mock_engine)

    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        parent_id="ROOT_PID",
        sources=[SourceFolderConfig(path=str(src_dir), pattern="*.zip")],
    )
    task = make_source_backup_task(job, job.sources[0])

    # Run with force_upload=True
    params = JobRunParams(force_upload=True)
    res = task.run(params=params)
    assert res["status"] == "uploaded"
    mock_engine.sync_file.assert_called_once()



