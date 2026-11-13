import logging
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import google_auth_httplib2
from googleapiclient.discovery import Resource, build
from googleapiclient.http import build_http

from config import BackupSettings, settings

logger = logging.getLogger(__name__)

# Scopes required to upload and manage backup files created by this app
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_credentials(
    app_settings: Optional[BackupSettings] = None,
    credentials_file: Optional[Path | str] = None,
    token_file: Optional[Path | str] = None,
) -> Credentials:
    """Load, refresh, or initiate Google Drive OAuth credentials.

    If token_file exists, load credentials from it. If expired, automatically
    refresh them. If not present, initiate the local server authorization flow
    using credentials_file and save the newly minted token.
    """
    cfg = app_settings or settings
    creds: Optional[Credentials] = None
    token_path = Path(token_file) if token_file else Path(cfg.token_file)
    creds_path = Path(credentials_file) if credentials_file else Path(cfg.credentials_file)

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            logger.warning("Could not load existing token from %s: %s", token_path, e)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google Drive credentials...")
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("Saved refreshed credentials to %s", token_path)
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"Google Drive client secrets file not found at '{creds_path}'. "
                    f"Please download your OAuth client credentials from Google Cloud Console "
                    f"and save it as '{creds_path}'."
                )

            logger.info("Starting local server OAuth flow to authorize Google Drive...")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)

            # Persist token for future headless runs
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("Google Drive token successfully saved to %s", token_path)

    return creds


def get_drive_service(
    app_settings: Optional[BackupSettings] = None,
    credentials_file: Optional[Path | str] = None,
    token_file: Optional[Path | str] = None,
) -> Resource:
    """Build and return an authenticated Google Drive v3 service client."""
    creds = get_drive_credentials(
        app_settings=app_settings,
        credentials_file=credentials_file,
        token_file=token_file,
    )
    # Use googleapiclient.http.build_http() so HTTP 308 ("Resume Incomplete" in
    # Google Drive resumable uploads) is excluded from httplib2.redirect_codes;
    # plain httplib2.Http() treats 308 as a Permanent Redirect and raises
    # RedirectMissingLocation after the first 32 MiB chunk of a large file.
    # Also raise timeout from 60s to 300s for large chunk transfers.
    raw_http = build_http()
    raw_http.timeout = 300
    raw_http.redirect_codes = raw_http.redirect_codes - {308}
    http = google_auth_httplib2.AuthorizedHttp(creds, http=raw_http)
    return build("drive", "v3", http=http, cache_discovery=False)
