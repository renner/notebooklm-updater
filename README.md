# NotebookLM Updater

Sync configured source documents to a Google Drive folder as native Google Docs for use with NotebookLM.

The script can import single files, such as HTML release notes, and discover linked files of a configured type, such as PDFs from a documentation landing page. It imports each source as a Google Doc and records source metadata in a local manifest. Later runs only replace documents whose source has changed.

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

Use another source configuration with `--config`:

```bash
python notebooklm_updater.py --folder-id FOLDER_ID --config my-sources.json
```

The first run opens a browser so each user can authorize their own Google account, then saves the resulting OAuth token in `token.json`. Both that token and `credentials.json` are ignored by Git.

## Updates

The script stores source URLs, ETags, file sizes, content hashes where needed, and managed Google Drive IDs in `manifest.json`. When an upstream PDF or configured HTML page changes, it imports a replacement Google Doc, then deletes the prior managed Drive file. To force a document to be imported again, remove its change-detection fields from its entry in `manifest.json`.

## Sources

Configure sources in `sources.json`. A source can be a `single_file` or `discovered_files` entry.

Single-file sources import exactly one URL:

```json
{
   "name": "SUSE Multi-Linux Manager Server 5.2 Release Notes",
   "type": "single_file",
   "url": "https://www.suse.com/releasenotes/x86_64/multi-linux-manager/5.2/index.html",
   "filename": "SUSE Multi-Linux Manager Server 5.2 Release Notes.html",
   "mime_type": "text/html"
}
```

Discovered-file sources fetch `base_url`, parse links from that page, make each link absolute, then import files whose resolved URL path ends with `file_extension`:

```json
{
   "name": "SUSE Multi-Linux Manager 5.2 PDFs",
   "type": "discovered_files",
   "base_url": "https://documentation.suse.com/multi-linux-manager/",
   "include_url_prefix": "https://documentation.suse.com/multi-linux-manager/5.2/",
   "file_extension": ".pdf",
   "mime_type": "application/pdf"
}
```

`include_url_prefix` is optional. When set, it means only discovered files whose absolute URL starts with that prefix are imported. For example, with `file_extension` set to `.pdf`, all linked PDF files under that URL prefix are downloaded and converted, while matching PDF links outside the prefix are ignored. The prefix may be an absolute URL or a path resolved relative to the fetched `base_url`.
