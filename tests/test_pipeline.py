from fastapi.testclient import TestClient

import backup
from core import orchestrator


def test_pipeline_registration():
    pipelines = orchestrator.pipelines
    assert "google_drive_backup" in pipelines

    pipeline = pipelines["google_drive_backup"]
    assert pipeline.name == "Google Drive Backup"
    assert len(pipeline.tasks) == 1
    assert pipeline.tasks[0].id == "sync_google_drive"
    assert len(pipeline.triggers) == 1
    assert pipeline.triggers[0].id == "scheduled_sync"


def test_api_list_pipelines():
    client = TestClient(backup.app)
    response = client.get("/api/pipelines/")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    pipeline = next((p for p in data if p["id"] == "google_drive_backup"), None)
    assert pipeline is not None
    assert pipeline["name"] == "Google Drive Backup"
    assert len(pipeline["triggers"]) == 1
    assert pipeline["triggers"][0]["id"] == "scheduled_sync"


def test_api_input_schema():
    client = TestClient(backup.app)
    response = client.get("/api/pipelines/google_drive_backup/input-schema")
    assert response.status_code == 200

    schema = response.json()
    assert "properties" in schema
    assert "source_dir" in schema["properties"]
    assert "gdrive_folder_id" in schema["properties"]


def test_web_spa_root():
    client = TestClient(backup.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<div id=\"root\"></div>" in response.text
