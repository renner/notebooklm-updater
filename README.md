# NotebookLM Updater

Sync the latest SUSE Multi-Linux Manager documentation PDFs and configured HTML pages to a Google Drive folder as native Google Docs for use with NotebookLM.

The script discovers PDF manuals from the official documentation landing page and imports configured single-page HTML sources, including the Multi-Linux Manager and Multi-Linux Manager Proxy 5.2 release notes. It imports each source as a Google Doc and records source metadata in a local manifest. Later runs only replace documents whose source has changed.

## Setup

1. Obtain OAuth client credentials for a Google desktop application and place them in this directory as `credentials.json`. You can use credentials provided by the project, or create your own Google Cloud project, enable the Google Drive API, and create an OAuth 2.0 desktop client.
2. Create and activate a virtual environment, then install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

3. Copy the target Google Drive folder ID from its URL:

   ```text
   https://drive.google.com/drive/folders/FOLDER_ID
   ```

## Run

```bash
source .venv/bin/activate
python notebooklm_updater.py --folder-id FOLDER_ID
```

The first run opens a browser so each user can authorize their own Google account, then saves the resulting OAuth token in `token.json`. Both that token and `credentials.json` are ignored by Git.

## Updates

The script stores source URLs, ETags, file sizes, content hashes where needed, and managed Google Drive IDs in `manifest.json`. When an upstream PDF or configured HTML page changes, it imports a replacement Google Doc, then deletes the prior managed Drive file. To force a document to be imported again, remove its change-detection fields from its entry in `manifest.json`.

## HTML Sources

Configure single-page HTML documents in the `HTML_SOURCES` list near the top of `notebooklm_updater.py`. Each entry needs a `filename` (the Google Doc name) and a `url`. HTML is downloaded on each run and compared using a SHA-256 content hash, so unchanged pages are not re-imported.
