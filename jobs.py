from datetime import datetime, timedelta, timezone
import fnmatch
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field, field_validator, model_validator

from auth import get_drive_service, gmail_send_message
from config import settings
from core import Pipeline, Trigger, get_logger
from core.pipeline.tasks import Task
from drive_sync import DriveSyncEngine, SyncSummary


logger = logging.getLogger(__name__)


UTC_PLUS_7 = timezone(timedelta(hours=7))


def current_month_folder_name(fmt: str = "%m", now: Optional[datetime] = None) -> str:
    """Month folder name based on the current date in UTC+7."""
    return (now or datetime.now(UTC_PLUS_7)).astimezone(UTC_PLUS_7).strftime(fmt)


def current_day_folder_name(fmt: str = "%d", now: Optional[datetime] = None) -> str:
    """Day folder name based on the current date in UTC+7."""
    return (now or datetime.now(UTC_PLUS_7)).astimezone(UTC_PLUS_7).strftime(fmt)


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
    destination_email: Optional[str] = Field(
        default=None,
        description="Destination email address to notify when the job finishes",
    )
    parent_id: Optional[str] = Field(
        default=None,
        description="Optional Google Drive parent folder ID (e.g. from folder URL)",
    )
    gdrive_parent_folder_id: Optional[str] = Field(
        default=None,
        description="Optional Google Drive parent folder ID (alias for parent_id)",
    )
    target_folder: Optional[str] = Field(
        default=None,
        description="Optional legacy destination folder name on Google Drive",
    )
    month_folder_format: Optional[str] = Field(
        default="%m",
        description="strftime format of the per-month subfolder (UTC+7). Empty disables it.",
    )
    day_folder_format: Optional[str] = Field(
        default="%d",
        description="strftime format of the per-day subfolder (UTC+7). Empty disables it.",
    )
    expected_daily_files: Optional[int] = Field(
        default=None,
        description="Expected number of files in the day folder (defaults to number of sources, e.g. 2). If day folder already has this many files, upload flow is skipped.",
    )
    schedule_hours: Optional[int] = Field(
        default=24,
        description="Scheduled interval in hours (used if cron is not set)",
    )
    cron: Optional[str] = Field(
        default=None,
        description="Optional cron schedule expression (e.g. '0 2 * * *')",
    )
    run_on_startup: bool = Field(
        default=False,
        description="Whether to also trigger this job immediately when the server starts",
    )
    enabled: bool = Field(default=True, description="Whether this job is active")

    @model_validator(mode="before")
    @classmethod
    def harmonize_parent_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            pid = data.get("parent_id")
            if pid is None or (isinstance(pid, str) and not pid.strip()):
                pid = data.get("gdrive_parent_folder_id")
            if isinstance(pid, str):
                pid = pid.strip() or None
            data["parent_id"] = pid
            data["gdrive_parent_folder_id"] = pid
        return data

    @model_validator(mode="after")
    def sync_parent_id(self) -> "JobConfig":
        pid = self.parent_id or self.gdrive_parent_folder_id
        if isinstance(pid, str):
            pid = pid.strip() or None
        self.parent_id = pid
        self.gdrive_parent_folder_id = pid
        return self

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

    gdrive_parent_folder_id: Optional[str] = Field(
        default=None,
        title="Folder parent id",
        description="Optional override for parent Google Drive folder ID",
    )
    credentials_file: Optional[str] = Field(
        default=None,
        title="Credential file",
        description="Optional override for client secrets JSON file",
    )
    destination_email: Optional[str] = Field(
        default=None,
        title="Destination email",
        description="Optional override for recipient email address",
    )
    force_upload: bool = Field(
        default=False,
        title="Force upload",
        description="Bypass folder file count/existence checks and force upload",
    )

    @model_validator(mode="before")
    @classmethod
    def harmonize_input_parent_id(cls, data: Any) -> Any:
        if isinstance(data, dict):
            pid = data.get("parent_id")
            if pid is not None and "gdrive_parent_folder_id" not in data:
                data["gdrive_parent_folder_id"] = pid
        return data

    @property
    def parent_id(self) -> Optional[str]:
        return self.gdrive_parent_folder_id


def make_job_run_params_class(job: JobConfig) -> type[JobRunParams]:
    """Create a JobRunParams subclass with job-specific defaults for the Web UI."""
    default_parent = job.parent_id or job.gdrive_parent_folder_id or None
    default_creds = job.credentials_file or "credentials.json"
    default_email = job.destination_email

    class ConfiguredJobRunParams(JobRunParams):
        gdrive_parent_folder_id: Optional[str] = Field(
            default=default_parent,
            title="Folder parent id",
            description="Optional override for parent Google Drive folder ID",
        )
        credentials_file: Optional[str] = Field(
            default=default_creds,
            title="Credential file",
            description="Optional override for client secrets JSON file",
        )
        destination_email: Optional[str] = Field(
            default=default_email,
            title="Destination email",
            description="Optional override for recipient email address",
        )

    ConfiguredJobRunParams.__name__ = f"{job.id}RunParams"
    return ConfiguredJobRunParams


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

    def run_backup_source(params: Optional[JobRunParams] = None) -> dict:
        try:
            log = get_logger()
        except Exception:
            log = logger
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

        # Reload latest config from disk if available to pick up live updates
        current_job = job
        try:
            latest_jobs = load_all_jobs()
            matching = next((j for j in latest_jobs if j.id == job.id), None)
            if matching:
                current_job = matching
        except Exception as e:
            log.warning("Could not reload latest job config from disk: %s", e)

        user_parent_id = None
        if params and params.gdrive_parent_folder_id and params.gdrive_parent_folder_id.strip():
            user_parent_id = params.gdrive_parent_folder_id.strip()

        job_parent_id = None
        if current_job.parent_id and current_job.parent_id.strip():
            job_parent_id = current_job.parent_id.strip()
        elif current_job.gdrive_parent_folder_id and current_job.gdrive_parent_folder_id.strip():
            job_parent_id = current_job.gdrive_parent_folder_id.strip()

        settings_parent_id = None
        if settings.gdrive_folder_id and settings.gdrive_folder_id.strip():
            settings_parent_id = settings.gdrive_folder_id.strip()

        effective_parent_id = user_parent_id or job_parent_id or settings_parent_id

        # If user explicitly supplied a parent folder ID and it differs from job config, persist it
        if user_parent_id and user_parent_id != job_parent_id:
            try:
                saved_path = update_job_parent_id_in_file(job.id, user_parent_id)
                if saved_path:
                    log.info(
                        "[%s] Persisted new parent_id '%s' to configuration file (%s)",
                        current_job.name,
                        user_parent_id,
                        saved_path,
                    )
                    current_job.parent_id = user_parent_id
                    current_job.gdrive_parent_folder_id = user_parent_id
            except Exception as e:
                log.warning("Failed to persist parent_id to configuration file: %s", e)

        effective_creds_file = (
            params.credentials_file
            if params and params.credentials_file
            else (current_job.credentials_file or str(settings.credentials_file))
        )
        effective_token_file = current_job.token_file or str(settings.token_file)

        # Connect to Google Drive API
        log.info("Connecting to Google Drive API using credentials '%s'...", effective_creds_file)
        service = get_drive_service(
            settings,
            credentials_file=effective_creds_file,
            token_file=effective_token_file,
        )
        engine = DriveSyncEngine(service=service, log=log)

        # If a Drive folder ID was given (UI / job / settings), that folder is
        # the destination parent. Otherwise fall back to named target folder if set.
        if effective_parent_id:
            backup_folder_id = effective_parent_id
            log.info("Using given Google Drive parent folder ID: %s", backup_folder_id)
        elif current_job.target_folder:
            backup_folder_id = engine.get_or_create_subfolder(
                folder_name=current_job.target_folder,
                parent_id=None,
            )
            log.info(
                "Destination Google Drive parent folder '%s' ID: %s",
                current_job.target_folder,
                backup_folder_id,
            )
        else:
            backup_folder_id = None
            log.info("No parent folder ID specified; storing directly in Google Drive root.")

        # Put the file in a folder named after the current month (UTC+7),
        # reusing it if it already exists.
        month_name = None
        if current_job.month_folder_format:
            month_name = current_month_folder_name(current_job.month_folder_format)
            backup_folder_id = engine.get_or_create_subfolder(
                folder_name=month_name,
                parent_id=backup_folder_id,
            )
            log.info("Month folder '%s' ID: %s", month_name, backup_folder_id)

        # Put the file in a nested folder named after the day of current date (UTC+7),
        # reusing it if it already exists -> parent folder -> month -> day (e.g. abc/11/20)
        day_name = None
        if current_job.day_folder_format:
            day_name = current_day_folder_name(current_job.day_folder_format)
            backup_folder_id = engine.get_or_create_subfolder(
                folder_name=day_name,
                parent_id=backup_folder_id,
            )
            log.info("Day folder '%s' ID: %s", day_name, backup_folder_id)

        # Check existing files in the destination month/day folder
        remote_files = engine.list_remote_files(backup_folder_id)

        # Count non-folder files already in this folder
        remote_file_items = [
            f
            for f in remote_files.values()
            if f.get("mimeType") != "application/vnd.google-apps.folder"
        ]
        remote_file_count = len(remote_file_items)

        # Target threshold: expected_daily_files or number of sources (minimum 2)
        expected_count = (
            current_job.expected_daily_files
            if current_job.expected_daily_files is not None
            else (len(current_job.sources) if current_job.sources else 2)
        )
        if expected_count < 2 and (not current_job.sources or len(current_job.sources) >= 2):
            expected_count = 2

        folder_label = (
            f"{month_name}/{day_name}"
            if month_name and day_name
            else (day_name or month_name or "destination folder")
        )

        force_upload = getattr(params, "force_upload", False) if params else False

        if not force_upload:
            # Check 1: If 2 (or expected_count) files are already in this month/day folder, skip send file flow
            if remote_file_count >= expected_count:
                log.info(
                    "[%s] Google Drive folder '%s' already contains %d files (expected %d). "
                    "Skipping send file to Google Drive flow for '%s'.",
                    current_job.name,
                    folder_label,
                    remote_file_count,
                    expected_count,
                    latest_file.name,
                )
                return {
                    "source": task_name,
                    "file": latest_file.name,
                    "size_mb": round(file_size_mb, 2),
                    "size_bytes": file_size_bytes,
                    "modified_time": mtime_str,
                    "google_drive_folder": current_job.target_folder,
                    "google_drive_folder_id": backup_folder_id,
                    "month_folder": month_name,
                    "day_folder": day_name,
                    "status": "skipped",
                    "reason": f"Folder already contains {remote_file_count} files (threshold: {expected_count})",
                }

            # Check 2: If this specific file is already present in this month/day folder, skip send file flow
            existing_file = remote_files.get(latest_file.name)
            if (
                existing_file
                and existing_file.get("mimeType") != "application/vnd.google-apps.folder"
            ):
                log.info(
                    "[%s] File '%s' already exists in Google Drive folder '%s'. "
                    "Skipping send file to Google Drive flow.",
                    current_job.name,
                    latest_file.name,
                    folder_label,
                )
                return {
                    "source": task_name,
                    "file": latest_file.name,
                    "size_mb": round(file_size_mb, 2),
                    "size_bytes": file_size_bytes,
                    "modified_time": mtime_str,
                    "google_drive_folder": current_job.target_folder,
                    "google_drive_folder_id": backup_folder_id,
                    "month_folder": month_name,
                    "day_folder": day_name,
                    "status": "skipped",
                    "reason": f"File '{latest_file.name}' already exists in destination folder",
                }

        # Sync the file to Drive
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
        log.info("[%s] Finished backup for '%s': %s", current_job.name, task_name, status_str)

        return {
            "source": task_name,
            "file": latest_file.name,
            "size_mb": round(file_size_mb, 2),
            "size_bytes": file_size_bytes,
            "modified_time": mtime_str,
            "google_drive_folder": current_job.target_folder,
            "google_drive_folder_id": backup_folder_id,
            "month_folder": month_name,
            "day_folder": day_name,
            "status": status_str,
        }

    return Task(
        id=task_id,
        name=f"Backup {task_name}",
        description=f"Uploads latest {source.pattern} from {source.path} to Google Drive",
        run=run_backup_source,
    )


def format_file_size(size_bytes: int | float) -> str:
    """Format file size in bytes to human-readable string (GB, MB, KB, B)."""
    if size_bytes >= 1024 * 1024 * 1024:
        val = size_bytes / (1024 * 1024 * 1024)
        formatted = f"{val:.2f}"
        if formatted.endswith(".00"):
            formatted = formatted[:-3]
        elif formatted.endswith("0"):
            formatted = formatted[:-1]
        return f"{formatted}GB"
    elif size_bytes >= 1024 * 1024:
        val = size_bytes / (1024 * 1024)
        formatted = f"{val:.2f}"
        if formatted.endswith(".00"):
            formatted = formatted[:-3]
        elif formatted.endswith("0"):
            formatted = formatted[:-1]
        return f"{formatted}MB"
    elif size_bytes >= 1024:
        val = size_bytes / 1024
        formatted = f"{val:.2f}"
        if formatted.endswith(".00"):
            formatted = formatted[:-3]
        elif formatted.endswith("0"):
            formatted = formatted[:-1]
        return f"{formatted}KB"
    return f"{size_bytes}B"


def build_backup_email_content(
    files: List[dict],
    completion_time: Optional[datetime] = None,
) -> str:
    """Build the Vietnamese email body for completed backup jobs.

    Format:
    Backup của ngày ... đã hoàn thành vào lúc ...
    Với ... file:
    A: ...GB
    B: ...MB
    ...
    """
    now = completion_time or datetime.now(UTC_PLUS_7)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(UTC_PLUS_7)
    else:
        now = now.astimezone(UTC_PLUS_7)

    date_str = now.strftime("%d/%m/%Y")
    time_str = now.strftime("%H:%M:%S")

    lines = [
        f"Backup của ngày {date_str} đã hoàn thành vào lúc {time_str}",
        f"Với {len(files)} file:",
    ]
    for item in files:
        file_name = item.get("file") or item.get("name") or "unknown"
        size_bytes = item.get("size_bytes")
        if size_bytes is None and item.get("size_mb") is not None:
            size_bytes = int(item["size_mb"] * 1024 * 1024)
        size_str = format_file_size(size_bytes or 0)
        lines.append(f"{file_name}: {size_str}")

    return "\n".join(lines)


def make_email_notification_task(job: JobConfig, source_tasks: List[Task]) -> Task:
    """Create an orchestrator task that sends a completion notification email via Gmail API."""
    task_id = f"{job.id.lower()}_email_notification"
    task_name = f"{job.name} Email Notification"

    def run_send_email(
        params: Optional[JobRunParams] = None,
        context: Optional[Any] = None,
    ) -> dict:
        try:
            log = get_logger()
        except Exception:
            log = logger

        log.info("=" * 60)
        log.info("[%s] Preparing backup completion email notification...", job.name)

        # Reload latest config from disk if available to pick up live updates
        current_job = job
        try:
            latest_jobs = load_all_jobs()
            matching = next((j for j in latest_jobs if j.id == job.id), None)
            if matching:
                current_job = matching
        except Exception as e:
            log.warning("Could not reload latest job config from disk: %s", e)

        dest_email = None
        if params and params.destination_email and params.destination_email.strip():
            dest_email = params.destination_email.strip()
        elif current_job.destination_email:
            dest_email = current_job.destination_email

        if not dest_email:
            log.info("[%s] No destination email configured. Skipping email notification.", current_job.name)
            return {"status": "skipped", "reason": "No destination email configured"}

        file_items = []
        for src_task, src_cfg in zip(source_tasks, current_job.sources):
            task_output = None
            if context and hasattr(context, "get_output_data"):
                try:
                    task_output = context.get_output_data(src_task.id)
                except Exception as e:
                    log.warning("Could not retrieve task output for %s: %s", src_task.id, e)

            if isinstance(task_output, dict) and task_output.get("file"):
                file_items.append({
                    "name": src_cfg.name,
                    "file": task_output.get("file"),
                    "size_mb": task_output.get("size_mb"),
                    "size_bytes": task_output.get("size_bytes"),
                })
            else:
                try:
                    src_dir = Path(src_cfg.path)
                    latest_f = find_latest_matching_file(src_dir, src_cfg.pattern)
                    f_size = latest_f.stat().st_size
                    file_items.append({
                        "name": src_cfg.name,
                        "file": latest_f.name,
                        "size_mb": round(f_size / (1024 * 1024), 2),
                        "size_bytes": f_size,
                    })
                except Exception as e:
                    log.warning("[%s] Could not locate latest file for %s: %s", current_job.name, src_cfg.name, e)
                    file_items.append({
                        "name": src_cfg.name,
                        "file": src_cfg.name or "unknown",
                        "size_mb": 0,
                        "size_bytes": 0,
                    })

        now = datetime.now(UTC_PLUS_7)
        email_content = build_backup_email_content(file_items, completion_time=now)
        date_str = now.strftime("%d/%m/%Y")
        subject = f"[{current_job.name}] Backup của ngày {date_str} đã hoàn thành"

        log.info("[%s] Sending backup completion email to %s", current_job.name, dest_email)
        log.info("Subject: %s", subject)
        log.info("Body:\n%s", email_content)

        effective_creds_file = (
            params.credentials_file
            if params and params.credentials_file
            else (current_job.credentials_file or str(settings.credentials_file))
        )
        effective_token_file = current_job.token_file or str(settings.token_file)

        send_result = gmail_send_message(
            to=dest_email,
            subject=subject,
            content=email_content,
            credentials_file=effective_creds_file,
            token_file=effective_token_file,
        )

        if send_result:
            msg_id = send_result.get("id") if isinstance(send_result, dict) else send_result
            log.info("[%s] Email sent successfully! Message Id: %s", current_job.name, msg_id)
            return {
                "status": "sent",
                "message_id": msg_id,
                "to": dest_email,
                "content": email_content,
            }
        else:
            log.error("[%s] Failed to send email to %s via Gmail API", current_job.name, dest_email)
            return {
                "status": "failed",
                "to": dest_email,
                "error": "Failed to send email via Gmail API",
                "content": email_content,
            }

    return Task(
        id=task_id,
        name=task_name,
        description=f"Sends backup completion notification email to {job.destination_email} via Gmail API",
        run=run_send_email,
    )


def create_job_pipeline(job: JobConfig) -> Pipeline:
    """Construct a runnable Pipeline for the specified JobConfig."""
    if not job.sources:
        raise ValueError(f"Job '{job.id}' has no source directories configured.")

    # Create task for each source folder
    source_tasks: List[Task] = [make_source_backup_task(job, src) for src in job.sources]

    # Chain tasks sequentially: t0 >> t1 >> t2 ...
    for i in range(len(source_tasks) - 1):
        source_tasks[i] >> source_tasks[i + 1]

    tasks: List[Task] = list(source_tasks)

    # If destination_email is configured, append an email notification task downstream of all sources
    if job.destination_email:
        email_task = make_email_notification_task(job, source_tasks)
        for src_task in source_tasks:
            src_task >> email_task
        tasks.append(email_task)

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
                schedule=CronTrigger.from_crontab(job.cron, timezone=UTC_PLUS_7),
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
        or f"Automated backup for {job.name} uploading to Google Drive.",
        tasks=tasks,
        params=make_job_run_params_class(job),
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
    destination_email: "nguyenvankhan.itc@mail.com"
    parent_id: ""
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


def get_active_jobs_file(config_path: Optional[Path] = None) -> Path:
    """Determine the active jobs configuration file path (jobs.yml)."""
    if config_path and config_path.exists():
        return config_path

    default_path = Path("jobs.yml")
    if not default_path.exists():
        default_path.write_text(DEFAULT_JOBS_YAML, encoding="utf-8")
    return default_path


def update_job_parent_id_in_file(
    job_id: str, new_parent_id: str, config_path: Optional[Path] = None
) -> Optional[Path]:
    """Update or insert parent_id for a given job in jobs.yml file.

    Preserves formatting and comments by performing targeted line updates.
    """
    target_file = get_active_jobs_file(config_path)
    if not target_file or not target_file.exists():
        return None

    content = target_file.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    job_start = -1
    job_end = len(lines)
    in_target_job = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- id:") or stripped.startswith("id:"):
            val = stripped.split(":", 1)[1].strip().strip("\"'")
            if val == job_id:
                in_target_job = True
                job_start = i
            elif in_target_job:
                job_end = i
                break

    if not in_target_job:
        return None

    found_idx = -1
    token_file_idx = -1
    creds_file_idx = -1
    for idx in range(job_start, job_end):
        l_strip = lines[idx].strip()
        if l_strip.startswith("parent_id:") or l_strip.startswith("gdrive_parent_folder_id:"):
            found_idx = idx
            break
        if l_strip.startswith("token_file:"):
            token_file_idx = idx
        elif l_strip.startswith("credentials_file:"):
            creds_file_idx = idx

    indent = "    "
    new_line = f'{indent}parent_id: "{new_parent_id}"\n'

    if found_idx != -1:
        lines[found_idx] = new_line
    elif token_file_idx != -1:
        lines.insert(token_file_idx + 1, new_line)
    elif creds_file_idx != -1:
        lines.insert(creds_file_idx + 1, new_line)
    else:
        lines.insert(job_start + 1, new_line)

    target_file.write_text("".join(lines), encoding="utf-8")
    return target_file


def update_job_destination_email_in_file(
    job_id: str, new_email: str, config_path: Optional[Path] = None
) -> Optional[Path]:
    """Update or insert destination_email for a given job in jobs.yml file.

    Preserves formatting and comments by performing targeted line updates.
    """
    target_file = get_active_jobs_file(config_path)
    if not target_file or not target_file.exists():
        return None

    content = target_file.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    job_start = -1
    job_end = len(lines)
    in_target_job = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- id:") or stripped.startswith("id:"):
            val = stripped.split(":", 1)[1].strip().strip("\"'")
            if val == job_id:
                in_target_job = True
                job_start = i
            elif in_target_job:
                job_end = i
                break

    if not in_target_job:
        return None

    found_idx = -1
    token_file_idx = -1
    creds_file_idx = -1
    for idx in range(job_start, job_end):
        l_strip = lines[idx].strip()
        if l_strip.startswith("destination_email:"):
            found_idx = idx
            break
        if l_strip.startswith("token_file:"):
            token_file_idx = idx
        elif l_strip.startswith("credentials_file:"):
            creds_file_idx = idx

    indent = "    "
    new_line = f'{indent}destination_email: "{new_email}"\n'

    if found_idx != -1:
        lines[found_idx] = new_line
    elif token_file_idx != -1:
        lines.insert(token_file_idx + 1, new_line)
    elif creds_file_idx != -1:
        lines.insert(creds_file_idx + 1, new_line)
    else:
        lines.insert(job_start + 1, new_line)

    target_file.write_text("".join(lines), encoding="utf-8")
    return target_file


def load_all_jobs(config_path: Optional[Path] = None) -> List[JobConfig]:
    """Load all configured backup jobs from jobs.yml."""
    target_file = get_active_jobs_file(config_path)
    raw_text = target_file.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw_text) or {}

    file_config = JobsFileConfig.model_validate(parsed)
    return [j for j in file_config.jobs if j.enabled]
