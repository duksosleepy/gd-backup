#!/usr/bin/env python3
"""Google Drive Automated Backup Service with Web UI.

Run directly via:
    uv run backup.py
"""

from pathlib import Path
import uvicorn
from apscheduler.triggers.interval import IntervalTrigger

from auth import get_drive_service
from config import BackupInputParams, settings
from core import Trigger, get_app, get_logger, register_pipeline, task
from drive_sync import DriveSyncEngine
from jobs import create_job_pipeline, load_all_jobs


@task
def sync_google_drive(params: BackupInputParams) -> dict:
    """Task that synchronizes the local directory to Google Drive."""
    log = get_logger()
    log.info("Starting Google Drive backup task...")

    # Determine effective source folder and target Drive folder
    source_dir = Path(params.source_dir) if params.source_dir else settings.source_dir
    target_folder_id = (
        params.gdrive_folder_id
        if params.gdrive_folder_id is not None
        else settings.gdrive_folder_id
    )

    log.info("Source directory: %s", source_dir.resolve())
    log.info("Destination folder ID: %s", target_folder_id or "root")

    # Connect to Google Drive
    log.info("Connecting to Google Drive API v3...")
    service = get_drive_service(settings)

    # Initialize sync engine and execute
    engine = DriveSyncEngine(service=service, log=log)
    summary = engine.sync_directory(local_dir=source_dir, target_folder_id=target_folder_id)

    if summary.errors:
        err_msg = f"Backup completed with {len(summary.errors)} error(s): {summary.errors[0]}"
        log.error(err_msg)
        raise RuntimeError(err_msg)

    log.info(
        "Backup finished successfully: %d uploaded, %d updated, %d skipped.",
        summary.files_uploaded,
        summary.files_updated,
        summary.files_skipped,
    )
    return summary.model_dump()


# Register all custom jobs defined in jobs.yaml (e.g. L60SANGTAM)
configured_jobs = load_all_jobs()
job_pipelines = [register_pipeline(create_job_pipeline(job)) for job in configured_jobs]

# Register default generic backup pipeline and its APScheduler timeline trigger
pipeline = register_pipeline(
    id="google_drive_backup",
    name="Google Drive Backup",
    description="Automated recursive directory synchronization to Google Drive with live logs and run history.",
    tasks=[sync_google_drive],
    params=BackupInputParams,
    triggers=[
        Trigger(
            id="scheduled_sync",
            name=f"Interval ({settings.schedule_hours}h)",
            description=f"Automated backup every {settings.schedule_hours} hours",
            schedule=IntervalTrigger(hours=settings.schedule_hours),
        )
    ],
)

# FastAPI application instance with static UI and WebSocket support
app = get_app()


def main():
    """Start the backup scheduler and web dashboard."""
    print("=" * 65)
    print("  Google Drive Automated Backup Service")
    print(f"  Environment: {settings.environment.upper()}")
    print(f"  Web Dashboard running at: http://{settings.host}:{settings.port}")
    print(f"  Schedule: Every {settings.schedule_hours} hours")
    if configured_jobs:
        print("  Configured Jobs:")
        for job in configured_jobs:
            sched_info = f"cron {job.cron}" if job.cron else (f"every {job.schedule_hours}h" if job.schedule_hours else "manual")
            print(f"    - [{job.id}] {job.name} ({len(job.sources)} sources, {sched_info})")
    print("=" * 65)

    uvicorn.run(
        "backup:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
