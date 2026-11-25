import base64
from email.message import EmailMessage
import logging
import os
from pathlib import Path
from typing import List, Optional, Union

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import google_auth_httplib2
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import build_http

from config import BackupSettings, settings

logger = logging.getLogger(__name__)

# Scopes required to upload and manage backup files created by this app
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Scope required to send notification emails via Gmail API
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# Combined scopes required for full application operation (Google Drive + Gmail)
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_drive_credentials(
    app_settings: Optional[BackupSettings] = None,
    credentials_file: Optional[Path | str] = None,
    token_file: Optional[Path | str] = None,
) -> Credentials:
    """Load, refresh, or initiate Google Drive OAuth credentials.

    If token_file exists, load credentials from it. If expired, automatically
    refresh them. If not present or missing required scopes, initiate the local server
    authorization flow using credentials_file and save the newly minted token.
    """
    cfg = app_settings or settings
    creds: Optional[Credentials] = None
    token_path = Path(token_file) if token_file else Path(cfg.token_file)
    creds_path = Path(credentials_file) if credentials_file else Path(cfg.credentials_file)

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path))
            if not creds.has_scopes(DRIVE_SCOPES):
                logger.warning(
                    "Token at %s does not contain required Google Drive scope %s. Re-authorization required.",
                    token_path,
                    DRIVE_SCOPES,
                )
                creds = None
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

            logger.info("Starting local server OAuth flow to authorize Google Drive & Gmail...")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)

            # Persist token for future headless runs
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("Google OAuth token successfully saved to %s", token_path)

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


def get_gmail_credentials(
    credentials_file: Optional[Path | str] = None,
    token_file: Optional[Path | str] = None,
) -> Credentials:
    """Load or refresh Google OAuth credentials for Gmail API."""
    creds: Optional[Credentials] = None

    token_path = Path(token_file) if token_file else (Path("token.json") if Path("token.json").exists() else Path("tokens.json"))
    creds_path = Path(credentials_file) if credentials_file else Path("credentials.json")

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path))
            if not creds.has_scopes(GMAIL_SCOPES):
                logger.warning(
                    "Token at %s does not contain required Gmail scope %s. Re-authorization required.",
                    token_path,
                    GMAIL_SCOPES,
                )
                creds = None
        except Exception as e:
            logger.warning("Could not load token from %s: %s", token_path, e)
            creds = None

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing expired Google credentials for Gmail...")
        creds.refresh(Request())
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        logger.info("Saved refreshed credentials to %s", token_path)

    if not creds or not creds.valid:
        try:
            creds, _ = google.auth.default(scopes=SCOPES)
        except Exception as e:
            logger.debug("google.auth.default() failed: %s", e)

    if not creds or not creds.valid:
        if creds_path.exists():
            logger.info("Starting local server OAuth flow to authorize Gmail and Google Drive...")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("Google OAuth token successfully saved to %s", token_path)

    if not creds:
        raise FileNotFoundError(
            f"Google credentials not found at {creds_path} or {token_path} with Gmail send scope."
        )

    return creds


def get_gmail_service(
    credentials_file: Optional[Path | str] = None,
    token_file: Optional[Path | str] = None,
) -> Resource:
    """Build and return an authenticated Gmail v1 service client."""
    creds = get_gmail_credentials(
        credentials_file=credentials_file,
        token_file=token_file,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def gmail_send_message(
    to: str,
    subject: str,
    content: str,
    credentials_file: Optional[Path | str] = None,
    token_file: Optional[Path | str] = None,
) -> Optional[dict]:
    """Create and send an email message via Gmail API."""
    try:
        service = get_gmail_service(
            credentials_file=credentials_file,
            token_file=token_file,
        )
        message = EmailMessage()
        message.set_content(content)
        message["To"] = to
        message["Subject"] = subject

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {"raw": encoded_message}

        send_message = (
            service.users()
            .messages()
            .send(userId="me", body=create_message)
            .execute()
        )
        print(f'Message Id: {send_message["id"]}')
        return send_message
    except HttpError as error:
        print(f"An error occurred: {error}")
        return None


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Authenticate Google Drive and Gmail API")
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to credentials.json file (default: credentials.json)",
    )
    parser.add_argument(
        "--token",
        default="token.json",
        help="Path to token.json file (default: token.json)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Initiating Google OAuth authorization for Drive and Gmail...")
    print(f"Credentials file: {args.credentials}")
    print(f"Token file:       {args.token}")
    print(f"Required scopes:  {SCOPES}")
    print("=" * 60)

    flow = InstalledAppFlow.from_client_secrets_file(args.credentials, SCOPES)
    new_creds = flow.run_local_server(port=0)

    with open(args.token, "w", encoding="utf-8") as f:
        f.write(new_creds.to_json())

    print(f"Successfully authenticated and saved token to '{args.token}'!")
    print(f"Granted scopes: {new_creds.scopes}")



