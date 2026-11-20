from fastapi.testclient import TestClient

import backup
from core import orchestrator


def test_pipeline_registration():
    pipelines = orchestrator.pipelines
    assert "L60SANGTAM" in pipelines

    l60 = pipelines["L60SANGTAM"]
    assert l60.name == "L60SANGTAM"
    assert len(l60.tasks) == 2
    assert l60.tasks[0].name == "Backup L60SANGTAM_HRM"
    assert l60.tasks[1].name == "Backup L60SANGTAM"
    assert len(l60.triggers) == 1


def test_api_list_pipelines():
    client = TestClient(backup.app)
    response = client.get("/api/pipelines/")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    l60 = next((p for p in data if p["id"] == "L60SANGTAM"), None)
    assert l60 is not None
    assert l60["name"] == "L60SANGTAM"
    assert len(l60["tasks"]) == 2


def test_api_l60sangtam_schema():
    client = TestClient(backup.app)
    response = client.get("/api/pipelines/L60SANGTAM/input-schema")
    assert response.status_code == 200

    schema = response.json()
    assert "properties" in schema
    assert "gdrive_parent_folder_id" in schema["properties"]
    assert "credentials_file" in schema["properties"]
    assert "target_folder" not in schema["properties"]
    assert "dry_run" not in schema["properties"]
    assert schema["properties"]["gdrive_parent_folder_id"]["title"] == "Folder parent id"
    assert schema["properties"]["credentials_file"]["title"] == "Credential file"


def test_api_configured_job_parent_id_schema():
    from jobs import JobConfig, create_job_pipeline
    from core import register_pipeline

    custom_job = JobConfig(
        id="CUSTOM_PIPELINE_TEST",
        name="Custom Pipeline Test",
        parent_id="PRECONFIGURED_PARENT_ID",
        sources=[r"E:\Test"],
    )
    register_pipeline(create_job_pipeline(custom_job))

    client = TestClient(backup.app)
    response = client.get("/api/pipelines/CUSTOM_PIPELINE_TEST/input-schema")
    assert response.status_code == 200

    schema = response.json()
    assert schema["properties"]["gdrive_parent_folder_id"]["default"] == "PRECONFIGURED_PARENT_ID"


def test_web_spa_root():
    client = TestClient(backup.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<div id=\"root\"></div>" in response.text
