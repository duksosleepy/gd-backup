import base64
from datetime import datetime
from email import message_from_bytes
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from auth import (
    DRIVE_SCOPES,
    GMAIL_SCOPES,
    SCOPES,
    get_drive_credentials,
    get_gmail_credentials,
    get_gmail_service,
    gmail_send_message,
)
from jobs import (
    JobConfig,
    SourceFolderConfig,
    build_backup_email_content,
    create_job_pipeline,
    format_file_size,
    load_all_jobs,
    make_email_notification_task,
    update_job_destination_email_in_file,
    UTC_PLUS_7,
)


def test_job_config_destination_email():
    job = JobConfig(
        id="TEST_JOB",
        name="Test Job",
        destination_email="nguyenvankhan.itc@mail.com",
        sources=[r"E:\Test"],
    )
    assert job.destination_email == "nguyenvankhan.itc@mail.com"


def test_format_file_size():
    assert format_file_size(500) == "500B"
    assert format_file_size(1024) == "1KB"
    assert format_file_size(1536) == "1.5KB"
    assert format_file_size(1024 * 1024) == "1MB"
    assert format_file_size(int(1024 * 1024 * 350.5)) == "350.5MB"
    assert format_file_size(1024 * 1024 * 500) == "500MB"
    assert format_file_size(1024 * 1024 * 1024) == "1GB"
    assert format_file_size(int(1024 * 1024 * 1024 * 1.25)) == "1.25GB"
    assert format_file_size(int(1024 * 1024 * 1024 * 2.0)) == "2GB"


def test_build_backup_email_content():
    files = [
        {"file": "L60SANGTAM_HRM_20261123.zip", "size_bytes": int(1024 * 1024 * 1024 * 1.25)},
        {"file": "L60SANGTAM_20261123.zip", "size_bytes": 1024 * 1024 * 500},
    ]
    test_time = datetime(2026, 11, 23, 16, 33, 9, tzinfo=UTC_PLUS_7)
    content = build_backup_email_content(files, completion_time=test_time)

    expected = (
        "Backup của ngày 23/11/2026 đã hoàn thành vào lúc 16:33:09\n"
        "Với 2 file:\n"
        "L60SANGTAM_HRM_20261123.zip: 1.25GB\n"
        "L60SANGTAM_20261123.zip: 500MB"
    )
    assert content == expected


def test_create_job_pipeline_with_destination_email():
    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        destination_email="nguyenvankhan.itc@mail.com",
        sources=[
            SourceFolderConfig(name="L60SANGTAM_HRM", path=r"E:\LINKQ\L60SANGTAM_HRM"),
            SourceFolderConfig(name="L60SANGTAM", path=r"E:\LINKQ\L60SANGTAM"),
        ],
    )
    pipeline = create_job_pipeline(job)
    assert len(pipeline.tasks) == 3
    assert pipeline.tasks[0].id == "l60sangtam_l60sangtam_hrm"
    assert pipeline.tasks[1].id == "l60sangtam_l60sangtam"
    assert pipeline.tasks[2].id == "l60sangtam_email_notification"

    # Upstream checks: email task must be downstream of all source tasks
    upstream = pipeline.upstream_of("l60sangtam_email_notification")
    assert "l60sangtam_l60sangtam_hrm" in upstream
    assert "l60sangtam_l60sangtam" in upstream


def test_create_job_pipeline_without_destination_email():
    job = JobConfig(
        id="JOB_WITHOUT_NOTIFICATION",
        name="Job Without Notification",
        destination_email=None,
        sources=[
            SourceFolderConfig(name="SRC1", path=r"E:\Test1"),
            SourceFolderConfig(name="SRC2", path=r"E:\Test2"),
        ],
    )
    pipeline = create_job_pipeline(job)
    assert len(pipeline.tasks) == 2
    assert all(not t.id.endswith("_email_notification") for t in pipeline.tasks)


def test_gmail_send_message_success():
    mock_service = MagicMock()
    mock_messages = MagicMock()
    mock_send = MagicMock()
    mock_send.execute.return_value = {"id": "msg_12345", "threadId": "thread_123"}
    mock_messages.send.return_value = mock_send
    mock_service.users.return_value.messages.return_value = mock_messages

    with patch("auth.get_gmail_service", return_value=mock_service):
        result = gmail_send_message(
            to="nguyenvankhan.itc@mail.com",
            subject="Test Subject",
            content="Backup của ngày 23/11/2026 đã hoàn thành vào lúc 16:33:09\nVới 1 file:\nA: 1.25GB",
        )

        assert result == {"id": "msg_12345", "threadId": "thread_123"}
        mock_service.users().messages().send.assert_called_once()
        call_args = mock_service.users().messages().send.call_args
        assert call_args.kwargs["userId"] == "me"
        raw_b64 = call_args.kwargs["body"]["raw"]
        raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("utf-8"))
        parsed_msg = message_from_bytes(raw_bytes)
        assert parsed_msg["To"] == "nguyenvankhan.itc@mail.com"
        assert parsed_msg["Subject"] == "Test Subject"
        assert "Backup của ngày 23/11/2026" in parsed_msg.get_payload()


def test_gmail_send_message_httperror():
    mock_service = MagicMock()
    mock_messages = MagicMock()
    mock_send = MagicMock()

    resp = Response({"status": 403, "reason": "Insufficient Permission"})
    mock_send.execute.side_effect = HttpError(resp, b"Error")
    mock_messages.send.return_value = mock_send
    mock_service.users.return_value.messages.return_value = mock_messages

    with patch("auth.get_gmail_service", return_value=mock_service):
        result = gmail_send_message(
            to="nguyenvankhan.itc@mail.com",
            subject="Test Subject",
            content="Test content",
        )
        assert result is None


def test_make_email_notification_task_with_context():
    job = JobConfig(
        id="L60SANGTAM",
        name="L60SANGTAM",
        destination_email="nguyenvankhan.itc@mail.com",
        sources=[
            SourceFolderConfig(name="L60SANGTAM_HRM", path=r"E:\L60SANGTAM_HRM"),
            SourceFolderConfig(name="L60SANGTAM", path=r"E:\L60SANGTAM"),
        ],
    )
    src_tasks = [
        MagicMock(id="l60sangtam_l60sangtam_hrm"),
        MagicMock(id="l60sangtam_l60sangtam"),
    ]

    mock_context = MagicMock()
    mock_context.get_output_data.side_effect = [
        {"file": "L60SANGTAM_HRM_20261123.zip", "size_bytes": int(1.25 * 1024**3)},
        {"file": "L60SANGTAM_20261123.zip", "size_bytes": 500 * 1024 * 1024},
    ]

    email_task = make_email_notification_task(job, src_tasks)

    with patch("jobs.gmail_send_message") as mock_send:
        mock_send.return_value = {"id": "sent_123"}
        res = email_task.run(params=None, context=mock_context)
        assert res["status"] == "sent"
        assert res["message_id"] == "sent_123"
        assert res["to"] == "nguyenvankhan.itc@mail.com"
        assert "L60SANGTAM_HRM_20261123.zip: 1.25GB" in res["content"]
        assert "L60SANGTAM_20261123.zip: 500MB" in res["content"]
        assert "Với 2 file:" in res["content"]


def test_l60sangtam_in_jobs_yml():
    jobs = load_all_jobs()
    l60 = next((j for j in jobs if j.id == "L60SANGTAM"), None)
    assert l60 is not None
    assert l60.destination_email == "nguyenvankhan.itc@mail.com"


def test_update_job_destination_email_in_file(tmp_path):
    yaml_content = """jobs:
  - id: "MY_JOB"
    name: "My Job"
    credentials_file: "credentials.json"
    token_file: "token.json"
    sources:
      - path: 'E:\\Test'
"""
    test_yaml = tmp_path / "jobs.yml"
    test_yaml.write_text(yaml_content, encoding="utf-8")

    saved_file = update_job_destination_email_in_file(
        "MY_JOB", "new_recipient@mail.com", config_path=test_yaml
    )
    assert saved_file == test_yaml
    new_text = test_yaml.read_text(encoding="utf-8")
    assert 'destination_email: "new_recipient@mail.com"' in new_text


def test_get_gmail_credentials_with_valid_scopes(tmp_path):
    token_file = tmp_path / "token.json"
    token_content = """{
        "token": "valid_token",
        "refresh_token": "valid_refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",
        "scopes": ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/gmail.send"],
        "expiry": "2099-01-01T00:00:00Z"
    }"""
    token_file.write_text(token_content, encoding="utf-8")

    creds = get_gmail_credentials(token_file=token_file)
    assert creds is not None
    assert creds.has_scopes(GMAIL_SCOPES)


def test_get_gmail_credentials_missing_scope_triggers_flow(tmp_path):
    token_file = tmp_path / "token.json"
    creds_file = tmp_path / "credentials.json"
    token_content = """{
        "token": "valid_token",
        "refresh_token": "valid_refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",
        "scopes": ["https://www.googleapis.com/auth/drive.file"]
    }"""
    token_file.write_text(token_content, encoding="utf-8")
    creds_file.write_text('{"installed": {"client_id": "cid", "client_secret": "sec"}}', encoding="utf-8")

    mock_flow = MagicMock()
    mock_new_creds = MagicMock()
    mock_new_creds.valid = True
    mock_new_creds.scopes = SCOPES
    mock_new_creds.to_json.return_value = '{"token": "new"}'
    mock_flow.run_local_server.return_value = mock_new_creds

    with patch("auth.InstalledAppFlow.from_client_secrets_file", return_value=mock_flow) as mock_from_secrets:
        creds = get_gmail_credentials(credentials_file=creds_file, token_file=token_file)
        mock_from_secrets.assert_called_once_with(str(creds_file), SCOPES)
        mock_flow.run_local_server.assert_called_once_with(port=0)
        assert creds == mock_new_creds


def test_get_drive_credentials_missing_scope_triggers_flow(tmp_path):
    token_file = tmp_path / "token.json"
    creds_file = tmp_path / "credentials.json"
    token_content = """{
        "token": "valid_token",
        "refresh_token": "valid_refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"]
    }"""
    token_file.write_text(token_content, encoding="utf-8")
    creds_file.write_text('{"installed": {"client_id": "cid", "client_secret": "sec"}}', encoding="utf-8")

    mock_flow = MagicMock()
    mock_new_creds = MagicMock()
    mock_new_creds.valid = True
    mock_new_creds.scopes = SCOPES
    mock_new_creds.to_json.return_value = '{"token": "new"}'
    mock_flow.run_local_server.return_value = mock_new_creds

    with patch("auth.InstalledAppFlow.from_client_secrets_file", return_value=mock_flow) as mock_from_secrets:
        creds = get_drive_credentials(credentials_file=creds_file, token_file=token_file)
        mock_from_secrets.assert_called_once_with(str(creds_file), SCOPES)
        mock_flow.run_local_server.assert_called_once_with(port=0)
        assert creds == mock_new_creds


