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

## Custom Backup Jobs (`jobs.yaml`)

Backup jobs are configured via [`jobs.yaml`](file:///home/duk/code/gd-backup/jobs.yaml). The service reads this file on startup and automatically constructs an independent orchestrator pipeline, scheduler trigger, and dashboard interface for each job.

### Example: `L60SANGTAM`
```yaml
jobs:
  - id: "L60SANGTAM"
    name: "L60SANGTAM"
    description: "Backup latest .zip from L60SANGTAM_HRM and L60SANGTAM databases to Google Drive"
    credentials_file: "credentials.json"
    token_file: "token.json"
    parent_id: "" # Optional Google Drive parent folder ID (e.g. from folder URL)
    cron: "0 5 * * *" # Daily at 5:00 AM
    enabled: true
    sources:
      - name: "L60SANGTAM_HRM"
        path: 'E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM_HRM'
        pattern: "*.zip"
      - name: "L60SANGTAM"
        path: 'E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM'
        pattern: "*.zip"
```

Each configured job provides:
- **Parent Folder ID (`parent_id`)**: Specify a target Google Drive folder ID directly in `jobs.yaml` / `jobs.yml`. Both manual and scheduled automation runs consistently read this ID, and manual runs will auto-populate and persist overrides to keep all runs synchronized.
- **Custom Credentials**: Each job can read its own `credentials_file` and `token_file` (allowing different Google accounts or service identities per job).
- **Multiple Sources**: Define multiple folders per job; the engine sequentially finds the latest matching archive in each folder and uploads it.
- **Smart Diff & Daily File Check**: Checks the destination `month/day` folder on Google Drive. If the folder already contains 2 files (or `expected_daily_files` / number of sources), or if the file already exists, it immediately skips the send file flow without redundant local MD5 hashing or API uploads.
- **Flexible Scheduling**: Supports both `schedule_hours: <int>` (interval) and standard `cron: "0 2 * * *"` expressions.

---

## Running as a Service with Mise Daemons (Production & Windows)

This project is configured with [`mise daemons`](https://mise.jdx.dev) and Pitchfork for supervised background process management in production, **fully compatible with Windows OS** as well as Linux and macOS.

### 1. Windows Compatibility Design
- **No POSIX `exec`**: Commands execute natively via `cmd /C` on Windows without Unix-only shell built-ins.
- **Native TCP Socket Probing**: Replaced shell-dependent `curl` commands with native TCP socket checks (`ready_port = 8100`, `ready_port = 4173`), preventing PowerShell alias incompatibilities (`Invoke-WebRequest`).
- **Windows Paths**: Windows drive paths (such as `E:\LINKQ\Database\BACKUP\SQL2017EN\L60SANGTAM`) are parsed natively with standard `pathlib.Path`.

### 2. Services Defined
- **`backup`**: The Python FastAPI service + APScheduler background runner on port `8100` (`http://localhost:8100`).
- **`frontend`**: The production web dashboard on port `4173` (`http://localhost:4173`), with live reverse proxying of API and WebSockets to the backend. It automatically depends on `backup` being healthy before starting.

### 3. Service Management Commands (PowerShell / CMD / Bash)

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

### 4. Windows Helper Scripts
For convenience on Windows, you can double-click or run from CMD / PowerShell:
- `start_daemons.bat`: Starts the background daemons using `mise daemons start`.
- `status_daemons.bat`: Checks daemon statuses (`mise daemons status`).
- `stop_daemons.bat`: Stops all background daemons (`mise daemons stop`).
- `run_backup.bat`: Runs standalone directly via `uv run backup.py`.

### 5. Auto-Start on System Boot (`pitchfork boot enable` & `boot_start = true`)
Both daemons (`backup` and `frontend`) in [`mise.toml`](file:///home/duk/code/gd-backup/mise.toml) and [`mise.production.toml`](file:///home/duk/code/gd-backup/mise.production.toml) have `boot_start = true` configured.

- **Enable Auto-Start on Login / Boot**:
  ```bash
  # Enable user-level auto-start on login:
  mise exec -- pitchfork boot enable

  # Or enable system-level service on boot (requires admin / sudo):
  sudo pitchfork boot enable
  ```
- **Check Boot Status**:
  ```bash
  mise exec -- pitchfork boot status
  ```
- **Disable Auto-Start**:
  ```bash
  mise exec -- pitchfork boot disable
  ```

---

## Running Tests

Run the automated test suite using `pytest`:

```bash
uv run pytest
```
