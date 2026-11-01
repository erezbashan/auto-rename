#!/usr/bin/env python3
"""
Automatically renames scanned documents/images placed in ~/Downloads/rename
using Gemini (following the user's Gem instructions) and moves them back to ~/Downloads.
"""

from __future__ import annotations

import base64
import fcntl
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path("/Users/erezbashan/workspace/renaming-scans")
VERSION = "1.1.0"
WATCH_DIR = Path("/tmp/rename-scans-inbox")
DEST_DIR = Path.home() / "Downloads"
FAILED_DIR = DEST_DIR / "rename_failed"
LOGS_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOGS_DIR / "renamer.log"
LOCK_FILE = Path("/tmp/rename-scans.lock")
ENV_FILE = PROJECT_DIR / ".env"
DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]

SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".txt": "text/plain",
}

IGNORED_NAMES = {".DS_Store", ".localized"}
IGNORED_SUFFIXES = {".part", ".crdownload", ".download", ".tmp"}

SYSTEM_INSTRUCTION = """Purpose and Goals:

* Assist users in renaming their documents efficiently based on file content.
* Ensure the new filenames follow a specific chronological and descriptive format.
* Automate the identification of key information such as dates and document summaries from provided text or images.

Behaviors and Rules:

1) Filename Structure:
a) The new name must begin with a date in 'yyyy-mm-dd' format extracted from the document's content.
b) Never put today's date if you don't know what the date is - just put the literal "yyyy-mm-dd" string in such a case.
c) Description: Following the date, provide a concise description of the document's subject matter. Make sure not to have any quote marks or path separators ('/', ':') in the description.
d) Language: The description must be written in the same language as the original document.
e) Formatting: Use spaces (' ') to separate the date and words in the description. Do not use underscores ('_').
f) File Suffix: Ensure the original file extension (e.g., .pdf, .jpg) is preserved and appended to the new filename.

2) Output Format:
Return a JSON object with a single key "new_filename" containing the complete new filename (including extension).
"""


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [v{VERSION}] [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("\"'")
    return env


def notify(title: str, message: str) -> None:
    try:
        escaped_msg = message.replace("\\", "\\\\").replace('"', '\\"')
        escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{escaped_msg}" with title "{escaped_title}"',
            ],
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def wait_until_file_ready(filepath: Path, timeout: float = 10.0) -> bool:
    """Wait until file size stops changing and is non-zero."""
    start = time.time()
    last_size = -1
    while time.time() - start < timeout:
        if not filepath.exists():
            return False
        size = filepath.stat().st_size
        if size > 0 and size == last_size:
            return True
        last_size = size
        time.sleep(0.4)
    return filepath.exists() and filepath.stat().st_size > 0


def upload_to_gemini_files_api(filepath: Path, mime_type: str, api_key: str) -> str:
    """Upload larger files via Gemini Files API and return the file_uri."""
    size = filepath.stat().st_size
    init_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    meta = json.dumps({"file": {"display_name": filepath.name}}).encode("utf-8")
    req = urllib.request.Request(init_url, data=meta, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        upload_url = resp.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Failed to obtain upload URL from Gemini Files API")

    data = filepath.read_bytes()
    upload_headers = {
        "Content-Length": str(size),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    }
    up_req = urllib.request.Request(upload_url, data=data, headers=upload_headers, method="POST")
    with urllib.request.urlopen(up_req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["file"]["uri"]


def generate_new_filename(filepath: Path, api_key: str, models: list[str]) -> str:
    ext = filepath.suffix.lower()
    mime_type = SUPPORTED_EXTENSIONS.get(ext) or mimetypes.guess_type(filepath.name)[0] or "application/octet-stream"

    size = filepath.stat().st_size
    if size > 15 * 1024 * 1024:
        file_uri = upload_to_gemini_files_api(filepath, mime_type, api_key)
        file_part = {"file_data": {"mime_type": mime_type, "file_uri": file_uri}}
    else:
        encoded = base64.b64encode(filepath.read_bytes()).decode("ascii")
        file_part = {"inline_data": {"mime_type": mime_type, "data": encoded}}

    user_prompt = (
        f"Original filename: {filepath.name}\n"
        f"Original extension: {filepath.suffix}\n"
        "Generate the new filename strictly following the system instructions."
    )

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    file_part,
                    {"text": user_prompt},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "new_filename": {
                        "type": "STRING",
                        "description": "The new filename in 'yyyy-mm-dd description.ext' format",
                    }
                },
                "required": ["new_filename"],
            },
        },
    }

    encoded_payload = json.dumps(payload).encode("utf-8")
    last_err: Exception | None = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=encoded_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            return parsed["new_filename"]
        except Exception as e:
            logging.warning("Model %s failed for %s (%s), trying next fallback...", model, filepath.name, e)
            last_err = e

    assert last_err is not None
    raise last_err


def sanitize_filename(raw_name: str, original_suffix: str) -> str:
    name = raw_name.strip()
    # Strip any accidental mv command wrapper if the model ever emitted one
    mv_match = re.match(r"^mv\s+(?:-[a-zA-Z]+\s+)*(?:'[^']*'|\"[^\"]*\"|\S+)\s+['\"]?(.+?)['\"]?$", name)
    if mv_match:
        name = mv_match.group(1).strip()

    # Remove quotes and forbidden filename characters on macOS
    name = name.replace("'", "").replace('"', "").replace("`", "")
    name = name.replace("/", "-").replace(":", "-").replace("\x00", "")
    name = name.replace("_", " ")
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()

    # Ensure original extension is preserved
    if original_suffix:
        if not name.lower().endswith(original_suffix.lower()):
            # Strip whatever known extension Gemini might have appended if different
            stem, ext = os.path.splitext(name)
            if ext.lower() in SUPPORTED_EXTENSIONS:
                name = stem
            name = f"{name}{original_suffix}"

    # Ensure it starts with yyyy-mm-dd or literal yyyy-mm-dd
    if not re.match(r"^(\d{4}-\d{2}-\d{2}|yyyy-mm-dd)\b", name):
        name = f"yyyy-mm-dd {name}"

    return name


def unique_destination_path(dest_dir: Path, filename: str) -> Path:
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def process_file(filepath: Path, api_key: str, models: list[str]) -> None:
    if not wait_until_file_ready(filepath):
        logging.warning("File %s disappeared or is empty, skipping.", filepath.name)
        return

    logging.info("Processing file: %s", filepath.name)
    try:
        raw_new_name = generate_new_filename(filepath, api_key, models)
        clean_name = sanitize_filename(raw_new_name, filepath.suffix)
        target_path = unique_destination_path(DEST_DIR, clean_name)

        shutil.move(str(filepath), str(target_path))
        logging.info("Renamed: '%s' -> '%s'", filepath.name, target_path.name)
        notify(f"Scan Renamed (v{VERSION})", f"{filepath.name} → {target_path.name}")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logging.error("Gemini API HTTP error for %s: %s - %s", filepath.name, e.code, err_body)
        move_to_failed(filepath)
        notify(f"Scan Rename Failed (v{VERSION})", f"API error on {filepath.name}")
    except Exception as e:
        logging.exception("Failed to process %s: %s", filepath.name, e)
        move_to_failed(filepath)
        notify(f"Scan Rename Failed (v{VERSION})", f"Error on {filepath.name}: {e}")


def move_to_failed(filepath: Path) -> None:
    try:
        if filepath.exists():
            FAILED_DIR.mkdir(parents=True, exist_ok=True)
            dest = unique_destination_path(FAILED_DIR, filepath.name)
            shutil.move(str(filepath), str(dest))
            logging.info("Moved failed file to %s", dest)
    except Exception as move_err:
        logging.error("Could not move failed file %s: %s", filepath, move_err)


def should_process(entry: Path) -> bool:
    if not entry.is_file():
        return False
    if entry.name.startswith(".") or entry.name in IGNORED_NAMES:
        return False
    if entry.suffix.lower() in IGNORED_SUFFIXES:
        return False
    if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
        logging.warning("Unsupported file extension for %s, moving back or skipping.", entry.name)
        return False
    return True


def ensure_downloads_symlink() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    symlink_path = DEST_DIR / "rename"
    if symlink_path.is_symlink():
        if symlink_path.resolve() != WATCH_DIR.resolve():
            symlink_path.unlink()
            symlink_path.symlink_to(WATCH_DIR)
    elif not symlink_path.exists():
        symlink_path.symlink_to(WATCH_DIR)


def main() -> int:
    setup_logging()
    ensure_downloads_symlink()

    lock_fd = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    env = load_env()
    api_key = os.environ.get("GEMINI_API_KEY") or env.get("GEMINI_API_KEY")
    custom_model = os.environ.get("GEMINI_MODEL") or env.get("GEMINI_MODEL")
    models = [custom_model] + [m for m in DEFAULT_MODELS if m != custom_model] if custom_model else list(DEFAULT_MODELS)

    if not api_key:
        logging.error("GEMINI_API_KEY not found in environment or %s", ENV_FILE)
        return 1

    # Watch if the user kept the folder at ~/Downloads/rename directly or via symlink
    direct_dir = DEST_DIR / "rename"
    dirs_to_scan = [WATCH_DIR]
    if direct_dir.exists() and not direct_dir.is_symlink():
        dirs_to_scan.append(direct_dir)

    # Process in loops until no more eligible files remain (handles files dropped mid-run)
    while True:
        files: list[Path] = []
        for d in dirs_to_scan:
            if d.exists():
                files.extend(p for p in d.iterdir() if should_process(p))
        files.sort(key=lambda p: p.stat().st_mtime)
        if not files:
            break
        for f in files:
            process_file(f, api_key, models)

    return 0


if __name__ == "__main__":
    sys.exit(main())
