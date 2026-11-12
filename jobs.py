from datetime import datetime
import fnmatch
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field, field_validator, model_validator

from auth import get_drive_service
from config import settings
from core import Pipeline, Trigger, get_logger
from core.pipeline.tasks import Task
from drive_sync import DriveSyncEngine, SyncSummary


logger = logging.getLogger(__name__)


class SourceFolderConfig(BaseModel):
    """Configuration for an individual backup source directory."""

    path: str = Field(description="Directory path on host system")
    pattern: str = Field(default="*.zip", description="File pattern to match (e.g. *.zip)")
    name: Optional[str] = Field(default=None, description="Display name for this backup source")

    @model_validator(mode="after")
    def set_default_name(self) -> "SourceFolderConfig":
        if not self.name and self.path:
            clean_path = self.path.rstrip("\\/").replace("\\", "/")
            parts = [p for p in clean_path.split("/") if p]
            self.name = parts[-1] if parts else "source"
        return self


class JobConfig(BaseModel):
    """Configuration for an automated backup job."""

    id: str = Field(description="Unique pipeline identifier (e.g. L60SANGTAM)")
    name: str = Field(description="Human-readable job name")
    description: Optional[str] = Field(
        default=None,
        description="Detailed description of what this job backs up",
    )
    sources: List[SourceFolderConfig] = Field(
        default_factory=list,
        description="List of source folders to include in this backup job",
    )
    credentials_file: Optional[str] = Field(
        default="credentials.json",
        description="Path to Google Cloud OAuth client secret JSON credentials file",
    )
    token_file: Optional[str] = Field(
        default="token.json",
        description="Path to Google Drive OAuth user token file",
    )
    target_folder: str = Field(
        default="backup",
        description="Destination folder name on Google Drive",
    )
    gdrive_parent_folder_id: Optional[str] = Field(
        default=None,
        description="Optional Google Drive parent folder ID (None uses configured root)",
    )
    schedule_hours: Optional[int] = Field(
        default=24,
        description="Scheduled interval in hours (used if cron is not set)",
    )
    cron: Optional[str] = Field(
        default=None,
        description="Optional cron schedule expression (e.g. '0 2 * * *')",
    )
    enabled: bool = Field(default=True, description="Whether this job is active")

    @field_validator("sources", mode="before")
    def parse_sources(cls, v):
        """Allow sources to be passed as list of strings or dicts."""
        if not isinstance(v, list):
            return v
        parsed = []
        for item in v:
            if isinstance(item, str):
                parsed.append(SourceFolderConfig(path=item))
            elif isinstance(item, dict):
                parsed.append(SourceFolderConfig(**item))
            elif isinstance(item, SourceFolderConfig):
                parsed.append(item)
        return parsed


class JobsFileConfig(BaseModel):
    """Root structure of the jobs configuration file."""

    jobs: List[JobConfig] = Field(default_factory=list)


class JobRunParams(BaseModel):
    """Parameters exposed in the Web UI for manual trigger executions."""

    target_folder: Optional[str] = Field(
        default=None,
        description="Optional override for destination folder name on Google Drive",
    )
    gdrive_parent_folder_id: Optional[str] = Field(
        default=None,
        description="Optional override for parent Google Drive folder ID",
    )
    credentials_file: Optional[str] = Field(
        default=None,
        description="Optional override for client secrets JSON file",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, inspects and logs latest files without uploading to Drive",
    )


def find_latest_matching_file(directory: Path, pattern: str = "*.zip") -> Path:
    """Find the latest modified file in a directory matching the specified pattern.

    Performs case-insensitive matching (e.g. matches both .zip and .ZIP).

    Raises:
        FileNotFoundError: If directory does not exist or contains no matching files.
        NotADirectoryError: If directory path is not a directory.
    """
    if not directory.exists():
        raise FileNotFoundError(f"Source directory does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {directory}")

    matching_files = [
        f
        for f in directory.iterdir()
        if f.is_file() and fnmatch.fnmatch(f.name.lower(), pattern.lower())
    ]

    if not matching_files:
        raise FileNotFoundError(
            f"No files matching pattern '{pattern}' found in {directory}"
        )

    return max(matching_files, key=lambda f: f.stat().st_mtime)


def make_source_backup_task(job: JobConfig, source: SourceFolderConfig) -> Task:
    """Create an orchestrator task that uploads the latest file from a single source folder."""
    task_name = source.name or Path(source.path).name or "Source Backup"
    task_slug = "".join(c if c.isalnum() else "_" for c in task_name.lower()).strip("_")
    task_id = f"{job.id.lower()}_{task_slug}"

    async def run_backup_source(params: Optional[JobRunParams] = None) -> dict:
        log = get_logger()
        log.info("=" * 60)
        log.info("[%s] Processing source: %s", job.name, task_name)
        log.info("Source directory: %s", source.path)
        log.info("Pattern filter: %s", source.pattern)

        source_dir = Path(source.path)
        latest_file = find_latest_matching_file(source_dir, source.pattern)

        file_size_bytes = latest_file.stat().st_size
        file_size_mb = file_size_bytes / (1024 * 1024)
        mtime_str = datetime.fromtimestamp(latest_file.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        log.info(
            "Found latest backup: '%s' (Size: %.2f MB, Modified: %s)",
            latest_file.name,
            file_size_mb,
            mtime_str,
        )

        effective_target_folder = (
            params.target_folder
            if params and params.target_folder
            else job.target_folder
        )
        effective_parent_id = (
            params.gdrive_parent_folder_id
            if params and params.gdrive_parent_folder_id is not None
            else (job.gdrive_parent_folder_id or settings.gdrive_folder_id)
        )
        is_dry_run = params.dry_run if params else False

        if is_dry_run:
            log.info("[DRY RUN] Would upload '%s' to Google Drive folder '%s'", latest_file.name, effective_target_folder)
            return {
                "source": task_name,
                "file": latest_file.name,
                "size_mb": round(file_size_mb, 2),
                "modified_time": mtime_str,
                "status": "dry_run",
            }

        effective_creds_file = (
            params.credentials_file
            if params and params.credentials_file
            else (job.credentials_file or str(settings.credentials_file))
        )
        effective_token_file = job.token_file or str(settings.token_file)

        # Connect to Google Drive API
        log.info("Connecting to Google Drive API using credentials '%s'...", effective_creds_file)
        service = get_drive_service(
            settings,
            credentials_file=effective_creds_file,
            token_file=effective_token_file,
        )
        engine = DriveSyncEngine(service=service, log=log)

        # Get or create target folder in Google Drive
        backup_folder_id = engine.get_or_create_subfolder(
            folder_name=effective_target_folder,
            parent_id=effective_parent_id,
        )
        log.info(
            "Destination Google Drive folder '%s' ID: %s",
            effective_target_folder,
            backup_folder_id,
        )

        # Sync the file to Drive
        remote_files = engine.list_remote_files(backup_folder_id)
        summary = SyncSummary(
            source_path=str(latest_file),
            target_folder_id=backup_folder_id,
        )
        engine.sync_file(
            local_file=latest_file,
            parent_folder_id=backup_folder_id,
            remote_files=remote_files,
            summary=summary,
        )

        status_str = (
            "uploaded"
            if summary.files_uploaded
            else ("updated" if summary.files_updated else "skipped")
        )
        log.info("[%s] Finished backup for '%s': %s", job.name, task_name, status_str)

        return {
            "source": task_name,
            "file": latest_file.name,
            "size_mb": round(file_size_mb, 2),
            "modified_time": mtime_str,
            "google_drive_folder": effective_target_folder,
            "google_drive_folder_id": backup_folder_id,
            "status": status_str,
        }

    return Task(
        id=task_id,
        name=f"Backup {task_name}",
        description=f"Uploads latest {source.pattern} from {source.path} to Google Drive",
        run=run_backup_source,
    )


def create_job_pipeline(job: JobConfig) -> Pipeline:
    """Construct a runnable Pipeline for the specified JobConfig."""
    if not job.sources:
        raise ValueError(f"Job '{job.id}' has no source directories configured.")

    # Create task for each source folder
    tasks: List[Task] = [make_source_backup_task(job, src) for src in job.sources]

    # Chain tasks sequentially: t0 >> t1 >> t2 ...
    for i in range(len(tasks) - 1):
        tasks[i] >> tasks[i + 1]

    # Configure schedule trigger
    triggers: List[Trigger] = []
    if job.cron:
        trigger_name = "Daily at 5:00 AM" if job.cron == "0 5 * * *" else f"Cron ({job.cron})"
        trigger_desc = "Automated backup every day at 5:00 AM" if job.cron == "0 5 * * *" else f"Scheduled via cron expression: {job.cron}"
        triggers.append(
            Trigger(
                id="cron_trigger",
                name=trigger_name,
                description=trigger_desc,
                schedule=CronTrigger.from_crontab(job.cron),
            )
        )
    elif job.schedule_hours and job.schedule_hours > 0:
        triggers.append(
            Trigger(
                id="scheduled_sync",
                name=f"Interval ({job.schedule_hours}h)",
                description=f"Automated backup every {job.schedule_hours} hours",
                schedule=IntervalTrigger(hours=job.schedule_hours),
            )
        )

    return Pipeline(
        id=job.id,
        name=job.name,
        description=job.description
        or f"Automated backup for {job.name} uploading to '{job.target_folder}' folder in Google Drive.",
        tasks=tasks,
        params=JobRunParams,
        triggers=triggers,
    )


DEFAULT_JOBS_YAML = """# GD Backup Jobs Configuration
# Add any number of custom backup jobs below.
jobs:
  - id: "L60SANGTAM"
    name: "L60SANGTAM"
    description: "Backup latest .zip from L60SANGTAM_HRM and L60SANGTAM databases to Google Drive"
    credentials_file: "credentials.json"
    token_file: "token.json"
    target_folder: "backup"
    cron: "0 5 * * *" # Daily at 5:00 AM
    enabled: true
    sources:
      - name: "L60SANGTAM_HRM"
        path: 'E:\\\\LINKQ\\\\Database\\\\BACKUP\\\\SQL2017EN\\\\L60SANGTAM_HRM'
        pattern: "*.zip"
      - name: "L60SANGTAM"
        path: 'E:\\\\LINKQ\\\\Database\\\\BACKUP\\\\SQL2017EN\\\\L60SANGTAM'
        pattern: "*.zip"
"""


def load_all_jobs(config_path: Optional[Path] = None) -> List[JobConfig]:
    """Load all configured backup jobs from YAML/JSON file or initialize default.

    Searches in order:
    1. Explicit config_path (if passed)
    2. jobs.yaml
    3. jobs.yml
    4. jobs.json
    If none found, creates and loads a default jobs.yaml containing L60SANGTAM.
    """
    candidates = [config_path] if config_path else [Path("jobs.yaml"), Path("jobs.yml"), Path("jobs.json")]

    target_file = None
    for cand in candidates:
        if cand and cand.exists():
            target_file = cand
            break

    if target_file is None:
        # Create default jobs.yaml
        default_path = Path("jobs.yaml")
        default_path.write_text(DEFAULT_JOBS_YAML, encoding="utf-8")
        target_file = default_path

    raw_text = target_file.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw_text) or {}

    file_config = JobsFileConfig.model_validate(parsed)
    return [j for j in file_config.jobs if j.enabled]
