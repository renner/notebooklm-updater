#!/usr/bin/env python3
"""
Downloads configured source documents and syncs them to a Google Drive folder
as Google Docs for use with NotebookLM.

First-time setup:
  1. Go to https://console.cloud.google.com/
  2. Create or select a project, enable the Google Drive API
  3. Go to APIs & Services > Credentials > Create Credentials > OAuth client ID
  4. Choose "Desktop app", download the JSON as credentials.json next to this script
  5. Find your Drive folder ID from its URL:
     https://drive.google.com/drive/folders/<FOLDER_ID>
  6. Run: python update_notebook_lm.py --folder-id <FOLDER_ID>

Subsequent runs reuse the saved token and only upload changed documents.
"""

import argparse
import hashlib
import json
import mimetypes
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
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
MANIFEST_FILE = "manifest.json"
CONFIG_FILE = "sources.json"
USER_AGENT = "notebook-lm-updater/1.0"


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


def load_config(config_path):
    return json.loads(Path(config_path).read_text())


def guess_mime_type(filename):
    mime_type, _ = mimetypes.guess_type(filename)
    if not mime_type:
        raise ValueError(f"Could not guess MIME type for {filename}")
    return mime_type


def normalize_extension(extension):
    return extension if extension.startswith(".") else f".{extension}"


def normalize_url(url):
    parsed = urlparse(url)
    if (parsed.scheme, parsed.port) not in (("http", 80), ("https", 443)):
        return url
    hostname = parsed.hostname or ""
    if parsed.username or parsed.password:
        userinfo = parsed.username or ""
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        hostname = f"{userinfo}@{hostname}"
    if parsed.port and parsed.port not in (80, 443):
        hostname = f"{hostname}:{parsed.port}"
    return parsed._replace(netloc=hostname).geturl()


def resolve_url_prefix(base_url, prefix):
    if not prefix:
        return None
    if urlparse(prefix).scheme:
        return normalize_url(prefix)
    return normalize_url(urljoin(base_url, prefix))


def single_file_documents(source):
    filename = source["filename"]
    return [
        {
            "filename": filename,
            "url": source["url"],
            "mime_type": source.get("mime_type") or guess_mime_type(filename),
            "source_name": source.get("name"),
        }
    ]


def discovered_file_documents(source):
    """Fetch a source index page and return linked files matching its filters."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    resp = session.get(source["base_url"])
    resp.raise_for_status()
    resolved_base_url = normalize_url(resp.url)
    include_url_prefix = resolve_url_prefix(
        resolved_base_url, source.get("include_url_prefix")
    )
    file_extension = normalize_extension(source["file_extension"])
    mime_type = source.get("mime_type") or guess_mime_type(file_extension)
    soup = BeautifulSoup(resp.text, "html.parser")

    documents = {}
    for a in soup.find_all("a", href=True):
        url = normalize_url(urljoin(resolved_base_url, a["href"]))
        if include_url_prefix and not url.startswith(include_url_prefix):
            continue
        if not urlparse(url).path.endswith(file_extension):
            continue
        filename = Path(urlparse(url).path).name
        documents[filename] = {
            "filename": filename,
            "url": url,
            "mime_type": mime_type,
            "source_name": source.get("name"),
        }

    return resolved_base_url, documents


def source_documents(source):
    source_type = source["type"]
    if source_type == "single_file":
        return source["url"], {doc["filename"]: doc for doc in single_file_documents(source)}
    if source_type == "discovered_files":
        return discovered_file_documents(source)
    raise ValueError(f"Unsupported source type: {source_type}")


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


def upload_to_drive(
    service, folder_id, filename, filepath, source_mime_type, drive_file_id=None
):
    """Import a source file as a Google Doc; return the file ID."""
    media = MediaFileUpload(filepath, mimetype=source_mime_type, resumable=True)
    file = service.files().create(
        body={
            "name": Path(filename).stem,
            "mimeType": GOOGLE_DOC_MIME_TYPE,
            "parents": [folder_id],
        },
        media_body=media,
        fields="id",
    ).execute()

    if drive_file_id:
        try:
            service.files().delete(fileId=drive_file_id).execute()
        except HttpError as error:
            if error.resp.status != 404:
                raise
            print(f"  uploaded {filename} (previous file was not found)")
        else:
            print(f"  updated  {filename}")
    else:
        print(f"  uploaded {filename}")
    return file["id"]


def sync_document(service, folder_id, manifest, tmpdir, document):
    """Import a configured document as a Google Doc when its content changes."""
    filename = document["filename"]
    url = document["url"]
    entry = manifest.get(filename)

    if not has_changed(url, entry):
        print(f"  skipped  {filename} (unchanged)")
        return False, True

    print(f"  downloading {filename} ...")
    dest = Path(tmpdir) / filename
    headers = download(url, str(dest))
    content_sha256 = hashlib.sha256(dest.read_bytes()).hexdigest()
    if entry and entry.get("content_sha256") == content_sha256:
        print(f"  skipped  {filename} (unchanged)")
        return False, True

    drive_id = upload_to_drive(
        service,
        folder_id,
        filename,
        str(dest),
        source_mime_type=document["mime_type"],
        drive_file_id=entry.get("drive_file_id") if entry else None,
    )
    manifest[filename] = {
        "url": url,
        "mime_type": document["mime_type"],
        "source_name": document.get("source_name"),
        "drive_file_id": drive_id,
        "etag": headers.get("ETag"),
        "size": headers.get("Content-Length"),
        "content_sha256": content_sha256,
    }
    save_manifest(manifest)
    return True, False


def main():
    parser = argparse.ArgumentParser(
        description="Sync configured source documents to Google Drive"
    )
    parser.add_argument(
        "--folder-id",
        required=True,
        help="Google Drive folder ID (from the folder's URL)",
    )
    parser.add_argument(
        "--config",
        default=CONFIG_FILE,
        help=f"JSON source configuration file (default: {CONFIG_FILE})",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    sources = config["sources"]
    documents = {}
    for source in sources:
        print(f"Discovering {source['name']} ...")
        source_url, source_docs = source_documents(source)
        documents.update(source_docs)
        print(f"  using {source_url}")
        print(f"  found {len(source_docs)} document(s)")
    print(f"\nFound {len(documents)} total document(s)\n")

    manifest = load_manifest()
    service = get_drive_service()

    updated = skipped = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for document in sorted(documents.values(), key=lambda item: item["filename"]):
            doc_updated, doc_skipped = sync_document(
                service, args.folder_id, manifest, tmpdir, document
            )
            updated += doc_updated
            skipped += doc_skipped

    print(f"\nDone: {updated} uploaded/updated, {skipped} skipped (unchanged)")


if __name__ == "__main__":
    main()
