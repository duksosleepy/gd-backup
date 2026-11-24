#!/usr/bin/env python3
"""Google Drive Automated Backup Service with Web UI.

Run directly via:
    uv run backup.py
"""

import uvicorn

from config import settings
from core import get_app, register_pipeline
from jobs import create_job_pipeline, load_all_jobs


# Register all custom jobs defined in jobs.yml (e.g. L60SANGTAM)
configured_jobs = load_all_jobs()
job_pipelines = [register_pipeline(create_job_pipeline(job)) for job in configured_jobs]

# FastAPI application instance with static UI and WebSocket support
app = get_app()


@app.on_event("startup")
async def log_and_verify_active_schedulers():
    """Log active scheduler triggers on server startup and handle startup runs."""
    import logging
    from core.orchestrator import orchestrator, run_pipeline_now

    log = logging.getLogger("backup")
    log.info("=" * 60)
    log.info("Active Automated Backup Schedulers:")
    for job in orchestrator.scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None)
        next_run_str = (
            next_run.strftime("%Y-%m-%d %H:%M:%S %Z") if next_run else "Manual / Pending"
        )
        log.info("  * Trigger [%s] -> Next fire time: %s", job.id, next_run_str)
        print(f"  * Trigger [{job.id}] -> Next fire time: {next_run_str}")
    log.info("=" * 60)

    # Automatically trigger immediate run for any job that has run_on_startup set
    for job_cfg in configured_jobs:
        if job_cfg.run_on_startup:
            target_pipeline = orchestrator.pipelines.get(job_cfg.id)
            if target_pipeline:
                log.info("[%s] Triggering immediate run on server startup...", job_cfg.id)
                await run_pipeline_now(target_pipeline, reason="startup")


def main():
    """Start the backup scheduler and web dashboard."""
    print("=" * 65)
    print("  Google Drive Automated Backup Service")
    print(f"  Environment: {settings.environment.upper()}")
    print(f"  Web Dashboard running at: http://{settings.host}:{settings.port}")
    print(f"  Schedule: Every {settings.schedule_hours} hours")
    if configured_jobs:
        print("  Configured Jobs (from jobs.yml):")
        for job in configured_jobs:
            sched_info = f"cron {job.cron}" if job.cron else (f"every {job.schedule_hours}h" if job.schedule_hours else "manual")
            print(f"    - [{job.id}] {job.name} ({len(job.sources)} sources, schedule: {sched_info})")
    print("=" * 65)

    uvicorn.run(
        "backup:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
