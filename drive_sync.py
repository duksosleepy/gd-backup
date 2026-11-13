import hashlib
import http.client
import logging
import mimetypes
from pathlib import Path
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import httplib2
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


UPLOAD_CHUNK_SIZE = 32 * 1024 * 1024  # must be a multiple of 256 KiB
MAX_CHUNK_RETRIES = 5
RETRYABLE_TRANSPORT_EXCEPTIONS = (
    httplib2.HttpLib2Error,
    http.client.HTTPException,
    ssl.SSLError,
    ConnectionError,
    TimeoutError,
    OSError,
)


class SyncSummary(BaseModel):
    """Execution summary of the directory synchronization process."""

    source_path: str
    target_folder_id: Optional[str] = None
    files_scanned: int = 0
    files_uploaded: int = 0
    files_updated: int = 0
    files_skipped: int = 0
    bytes_transferred: int = 0
    errors: List[str] = Field(default_factory=list)


def compute_file_md5(file_path: Path, chunk_size: int = 8192 * 1024) -> str:
    """Compute and return the hex MD5 checksum of a local file."""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            md5.update(chunk)
    return md5.hexdigest()


class DriveSyncEngine:
    """Handles synchronization between a local directory and a Google Drive folder."""

    def __init__(self, service: Resource, log: Optional[logging.Logger] = None):
        self.service = service
        self.log = log or logger
        self._folder_cache: Dict[Tuple[str, str], str] = {}

    def list_remote_files(self, parent_folder_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """List active files in the specified parent folder on Google Drive.

        Returns a dictionary mapping file name to its metadata dictionary.
        """
        if parent_folder_id:
            query = f"'{parent_folder_id}' in parents and trashed = false"
        else:
            query = "trashed = false and 'root' in parents"

        files_by_name: Dict[str, Dict[str, Any]] = {}
        page_token: Optional[str] = None

        while True:
            response = (
                self.service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, md5Checksum, size, modifiedTime, mimeType)",
                    pageToken=page_token,
                    pageSize=1000,
                )
                .execute(num_retries=5)
            )

            for item in response.get("files", []):
                files_by_name[item["name"]] = item

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return files_by_name

    def get_or_create_subfolder(self, folder_name: str, parent_id: Optional[str] = None) -> str:
        """Find or create a directory in Google Drive and return its folder ID."""
        cache_key = (folder_name, parent_id or "root")
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        if parent_id:
            query = f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and name = '{folder_name}' and trashed = false"
        else:
            query = f"'root' in parents and mimeType = 'application/vnd.google-apps.folder' and name = '{folder_name}' and trashed = false"

        response = self.service.files().list(q=query, fields="files(id, name)").execute(num_retries=5)
        files = response.get("files", [])

        if files:
            folder_id = files[0]["id"]
            self._folder_cache[cache_key] = folder_id
            return folder_id

        # Folder does not exist, create it
        body: Dict[str, Any] = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]

        created = self.service.files().create(body=body, fields="id").execute(num_retries=5)
        folder_id = created["id"]
        self.log.info("Created Google Drive folder '%s' (ID: %s)", folder_name, folder_id)
        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _upload_with_progress(self, request, filename: str, file_size: int) -> Dict[str, Any]:
        """Run a resumable upload chunk by chunk, retrying transient errors."""
        response = None
        last_pct = -1
        consecutive_errors = 0
        while response is None:
            try:
                status, response = request.next_chunk(num_retries=MAX_CHUNK_RETRIES)
                consecutive_errors = 0
            except (HttpError, *RETRYABLE_TRANSPORT_EXCEPTIONS) as exc:
                if isinstance(exc, HttpError) and exc.resp.status not in (408, 429, 500, 502, 503, 504):
                    raise
                request._in_error_state = True
                consecutive_errors += 1
                if consecutive_errors > MAX_CHUNK_RETRIES:
                    raise
                backoff = min(2 ** consecutive_errors, 60)
                self.log.warning(
                    "Transient error uploading '%s' (attempt %d/%d): %s. Retrying in %ds...",
                    filename,
                    consecutive_errors,
                    MAX_CHUNK_RETRIES,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
                continue

            if status:
                pct = int(status.progress() * 100)
                if pct // 10 != last_pct // 10:
                    self.log.info("Uploading '%s': %d%%", filename, pct)
                    last_pct = pct
        return response

    def sync_file(
        self,
        local_file: Path,
        parent_folder_id: Optional[str],
        remote_files: Dict[str, Dict[str, Any]],
        summary: SyncSummary,
    ) -> None:
        """Synchronize an individual file to Google Drive.

        - If absent on Drive: uploads new file.
        - If present and MD5 checksum matches: skips upload.
        - If present and MD5 differs: updates existing file in-place.
        """
        filename = local_file.name
        summary.files_scanned += 1
        file_size = local_file.stat().st_size
        local_md5 = compute_file_md5(local_file)
        mime_type, _ = mimetypes.guess_type(str(local_file))
        mime_type = mime_type or "application/octet-stream"

        remote_item = remote_files.get(filename)

        if remote_item:
            remote_md5 = remote_item.get("md5Checksum")
            if remote_md5 and remote_md5.lower() == local_md5.lower():
                self.log.info("Skipping '%s' (content matches remote MD5 %s)", filename, remote_md5[:8])
                summary.files_skipped += 1
                return

            # File modified - update in place
            self.log.info("Updating existing file '%s' on Google Drive (ID: %s)...", filename, remote_item["id"])
            media = MediaFileUpload(str(local_file), mimetype=mime_type, resumable=True, chunksize=UPLOAD_CHUNK_SIZE)
            request = self.service.files().update(
                fileId=remote_item["id"],
                media_body=media,
                fields="id, name, md5Checksum",
            )
            self._upload_with_progress(request, filename, file_size)
            self.log.info("Successfully updated '%s'", filename)
            summary.files_updated += 1
            summary.bytes_transferred += file_size
        else:
            # File missing - create on Drive
            self.log.info("Uploading new file '%s' (%d bytes)...", filename, file_size)
            body: Dict[str, Any] = {"name": filename}
            if parent_folder_id:
                body["parents"] = [parent_folder_id]

            media = MediaFileUpload(str(local_file), mimetype=mime_type, resumable=True, chunksize=UPLOAD_CHUNK_SIZE)
            request = self.service.files().create(
                body=body, media_body=media, fields="id, name, md5Checksum"
            )
            created = self._upload_with_progress(request, filename, file_size)
            self.log.info("Successfully uploaded '%s' (ID: %s)", filename, created.get("id"))
            summary.files_uploaded += 1
            summary.bytes_transferred += file_size

    def sync_directory(
        self,
        local_dir: Path,
        target_folder_id: Optional[str] = None,
    ) -> SyncSummary:
        """Recursively synchronize local directory files to Google Drive."""
        summary = SyncSummary(
            source_path=str(local_dir.resolve()),
            target_folder_id=target_folder_id,
        )

        if not local_dir.exists():
            error_msg = f"Source directory does not exist: {local_dir}"
            self.log.error(error_msg)
            summary.errors.append(error_msg)
            return summary

        if not local_dir.is_dir():
            error_msg = f"Source path is not a directory: {local_dir}"
            self.log.error(error_msg)
            summary.errors.append(error_msg)
            return summary

        self.log.info("Starting backup sync from '%s' to Google Drive folder %s", local_dir, target_folder_id or "root")

        # Map relative directories to remote Google Drive folder IDs
        # Relative directory '.' maps to target_folder_id
        folder_mapping: Dict[Path, Optional[str]] = {Path("."): target_folder_id}

        for path in sorted(local_dir.rglob("*")):
            if path.is_dir():
                # Ensure corresponding remote subfolder exists
                rel_dir = path.relative_to(local_dir)
                parent_rel = rel_dir.parent
                parent_remote_id = folder_mapping.get(parent_rel, target_folder_id)
                subfolder_id = self.get_or_create_subfolder(path.name, parent_remote_id)
                folder_mapping[rel_dir] = subfolder_id
            elif path.is_file():
                rel_dir = path.parent.relative_to(local_dir)
                parent_remote_id = folder_mapping.get(rel_dir, target_folder_id)

                # Fetch listing for this specific remote parent folder
                remote_files = self.list_remote_files(parent_remote_id)

                try:
                    self.sync_file(path, parent_remote_id, remote_files, summary)
                except Exception as e:
                    err = f"Failed to sync {path.name}: {e}"
                    self.log.error(err, exc_info=True)
                    summary.errors.append(err)

        self.log.info(
            "Backup complete: %d scanned, %d uploaded, %d updated, %d skipped, %d bytes transferred, %d errors",
            summary.files_scanned,
            summary.files_uploaded,
            summary.files_updated,
            summary.files_skipped,
            summary.bytes_transferred,
            len(summary.errors),
        )

        return summary
