# Google Drive Automated Backup Service

Automated recursive file backup from local storage to Google Drive on a scheduled timeline, featuring a local Web UI for monitoring schedules, inspecting execution history, streaming live logs, and triggering manual runs on demand.

## Features

- **Single Command Startup:** Run everything (scheduler + web dashboard) via `uv run backup.py`.
- **APScheduler Timeline:** Automated background sync configured via Python in `backup.py` (default: 24h interval).
- **Exact Dashboard Layout:** Clean web interface built with React and Tremor displaying:
  - Pipeline details & next run countdown
  - Real-time log streaming over WebSockets
  - Run duration, timestamps, and status badges (Success/Running/Failed)
  - Manual "Run" dialog with parameter override options
- **Zero Authentication Friction:** Open dashboard access on localhost with no login required.
- **Smart Directory Sync:**
  - Recursively syncs all files and subdirectories.
  - Compares local file MD5 checksums against Google Drive metadata.
  - Skips unchanged files to save bandwidth and API quota.
  - Updates modified files in place rather than generating duplicate copies.

---

## Quick Start

### 1. Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) package manager

### 2. Configure Google Cloud OAuth

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Google Drive API**.
3. Create OAuth 2.0 Client Credentials (Desktop app).
4. Download the credentials JSON and save it as `credentials.json` in this repository root.

### 3. Configure Settings (Optional)

Copy `.env.example` to `.env` to customize settings:

```bash
cp .env.example .env
```

| Environment Variable | Default | Description |
|---|---|---|
| `BACKUP_SOURCE_DIR` | `./data_to_backup` | Local directory to back up |
| `GDRIVE_FOLDER_ID` | `None` (Drive root) | Target Google Drive folder ID |
| `BACKUP_SCHEDULE_HOURS` | `24` | Scheduled sync interval in hours |
| `PORT` | `8100` | Web dashboard port |
| `HOST` | `0.0.0.0` | Server host address |

### 4. Run Application

```bash
uv run backup.py
```

On first launch without `token.json`, your browser will open to complete the Google OAuth consent flow. After granting access, the token is saved to `token.json` and future runs execute completely unattended.

Access the web dashboard in your browser:
👉 **`http://localhost:8100`**

---

## Running as a Service with Mise Daemons (Production)

This project is configured with [`mise daemons`](https://mise.jdx.dev) and Pitchfork for supervised background process management in production:

### 1. Services Defined
- **`backup`**: The Python FastAPI service + APScheduler background runner on port `8100` (`http://localhost:8100`).
- **`frontend`**: The production web dashboard on port `4173` (`http://localhost:4173`), with live reverse proxying of API and WebSockets to the backend. It automatically depends on `backup` being healthy before starting.

### 2. Service Management Commands

```bash
# Start all services in production mode
mise daemons start

# Check real-time daemon status
mise daemons status

# Inspect streaming daemon logs
mise daemons logs

# Stop all background services
mise daemons stop
```

You can also run with explicit environment overlay:
```bash
mise -E production daemons start
```

---

## Running Tests

Run the automated test suite using `pytest`:

```bash
uv run pytest
```
