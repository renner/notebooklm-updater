#!/usr/bin/env python3
"""
Downloads PDFs from the latest SUSE Multi-Linux Manager documentation
and syncs them to a Google Drive folder for use with NotebookLM.

First-time setup:
  1. Go to https://console.cloud.google.com/
  2. Create or select a project, enable the Google Drive API
  3. Go to APIs & Services > Credentials > Create Credentials > OAuth client ID
  4. Choose "Desktop app", download the JSON as credentials.json next to this script
  5. Find your Drive folder ID from its URL:
     https://drive.google.com/drive/folders/<FOLDER_ID>
  6. Run: python update_notebook_lm.py --folder-id <FOLDER_ID>

Subsequent runs reuse the saved token and only upload changed PDFs.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DOC_LANDING_URL = "https://documentation.suse.com/multi-linux-manager/"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
MANIFEST_FILE = "manifest.json"


def get_drive_service():
    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(CREDENTIALS_FILE).exists():
                print(f"Error: {CREDENTIALS_FILE} not found.")
                print()
                print("Setup instructions:")
                print("  1. Go to https://console.cloud.google.com/")
                print("  2. Create/select a project, enable the Google Drive API")
                print("  3. Create OAuth 2.0 credentials (Desktop app type)")
                print(f"  4. Download the JSON as '{CREDENTIALS_FILE}' next to this script")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(TOKEN_FILE).write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def find_pdf_urls():
    """Fetch the latest docs index and return its URL plus its PDF links."""
    session = requests.Session()
    session.headers["User-Agent"] = "notebook-lm-updater/1.0"

    resp = session.get(DOC_LANDING_URL)
    resp.raise_for_status()
    doc_base_url = resp.url
    doc_path_prefix = f"{urlparse(doc_base_url).path.rstrip('/')}/"
    soup = BeautifulSoup(resp.text, "html.parser")

    pdfs = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".pdf") and href.startswith(doc_path_prefix):
            url = urljoin("https://documentation.suse.com", href)
            filename = Path(urlparse(href).path).name
            pdfs[filename] = url

    return doc_base_url, pdfs


def load_manifest():
    if Path(MANIFEST_FILE).exists():
        return json.loads(Path(MANIFEST_FILE).read_text())
    return {}


def save_manifest(manifest):
    Path(MANIFEST_FILE).write_text(json.dumps(manifest, indent=2))


def has_changed(url, entry):
    """HEAD the URL and compare ETag/Content-Length against the manifest entry."""
    if not entry:
        return True
    resp = requests.head(url, allow_redirects=True)
    etag = resp.headers.get("ETag")
    if etag and etag == entry.get("etag"):
        return False
    size = resp.headers.get("Content-Length")
    if size and size == str(entry.get("size")):
        return False
    return True


def download(url, dest_path):
    """Download url to dest_path, return response headers."""
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    return resp.headers


def upload_to_drive(service, folder_id, filename, filepath, drive_file_id=None):
    """Create or update a file in Drive; return the file ID."""
    media = MediaFileUpload(filepath, mimetype="application/pdf", resumable=True)
    if drive_file_id:
        file = service.files().update(
            fileId=drive_file_id,
            media_body=media,
            fields="id",
        ).execute()
        print(f"  updated  {filename}")
    else:
        file = service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()
        print(f"  uploaded {filename}")
    return file["id"]


def main():
    parser = argparse.ArgumentParser(
        description="Sync the latest SUSE MLM documentation PDFs to Google Drive"
    )
    parser.add_argument(
        "--folder-id",
        required=True,
        help="Google Drive folder ID (from the folder's URL)",
    )
    args = parser.parse_args()

    print(f"Fetching latest PDF list from {DOC_LANDING_URL} ...")
    doc_base_url, pdfs = find_pdf_urls()
    print(f"Using documentation release at {doc_base_url}")
    print(f"Found {len(pdfs)} PDFs\n")

    manifest = load_manifest()
    service = get_drive_service()

    updated = skipped = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for filename, url in sorted(pdfs.items()):
            entry = manifest.get(filename)
            if not has_changed(url, entry):
                print(f"  skipped  {filename} (unchanged)")
                skipped += 1
                continue

            print(f"  downloading {filename} ...")
            dest = Path(tmpdir) / filename
            headers = download(url, str(dest))

            drive_id = upload_to_drive(
                service, args.folder_id, filename, str(dest),
                drive_file_id=entry.get("drive_file_id") if entry else None,
            )

            manifest[filename] = {
                "url": url,
                "drive_file_id": drive_id,
                "etag": headers.get("ETag"),
                "size": headers.get("Content-Length"),
            }
            save_manifest(manifest)
            updated += 1

    print(f"\nDone: {updated} uploaded/updated, {skipped} skipped (unchanged)")


if __name__ == "__main__":
    main()
