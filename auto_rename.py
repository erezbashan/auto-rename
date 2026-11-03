#!/usr/bin/env python3
"""
Auto Rename: Renames selected documents/images in-place using Gemini
(following the user's Gem instructions). Invoked via macOS Finder Quick Action
or directly from the command line with one or more file paths.
"""

from __future__ import annotations

import base64
import errno
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

PROJECT_DIR = Path("/Users/erezbashan/workspace/auto-rename")
VERSION = "2.5.0"
LOGS_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOGS_DIR / "renamer.log"
ENV_FILE = PROJECT_DIR / ".env"
DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
RENAMING_SUFFIX = ".renaming"

SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".txt": "text/plain",
}

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


def read_file_bytes_safely(filepath: Path, retries: int = 8, delay: float = 1.0) -> bytes:
    """
    Read file bytes, handling cloud-synced files (Google Drive / iCloud FileProvider)
    where renaming first or concurrent hydrations can trigger Errno 11 (EDEADLK).
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            return filepath.read_bytes()
        except OSError as e:
            last_err = e
            if e.errno in (errno.EDEADLK, errno.EAGAIN, errno.EBUSY):
                logging.warning(
                    "Cloud file provider busy/hydrating %s (errno %s, attempt %d/%d), waiting...",
                    filepath.name,
                    e.errno,
                    attempt + 1,
                    retries,
                )
                # Trigger macOS file provider materialization via brctl/mdls or subprocess cat if needed
                try:
                    res = subprocess.run(
                        ["/bin/cat", str(filepath)],
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    if res.returncode == 0 and res.stdout:
                        return res.stdout
                except Exception:
                    pass
                time.sleep(delay)
                continue
            raise
    assert last_err is not None
    raise last_err


def upload_to_gemini_files_api(file_bytes: bytes, display_name: str, mime_type: str, api_key: str) -> str:
    """Upload larger files via Gemini Files API and return the file_uri."""
    size = len(file_bytes)
    init_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    meta = json.dumps({"file": {"display_name": display_name}}).encode("utf-8")
    req = urllib.request.Request(init_url, data=meta, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        upload_url = resp.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Failed to obtain upload URL from Gemini Files API")

    upload_headers = {
        "Content-Length": str(size),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    }
    up_req = urllib.request.Request(upload_url, data=file_bytes, headers=upload_headers, method="POST")
    with urllib.request.urlopen(up_req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["file"]["uri"]


def generate_new_filename(
    file_bytes: bytes,
    original_name: str,
    original_suffix: str,
    api_key: str,
    models: list[str],
) -> str:
    ext = original_suffix.lower()
    mime_type = SUPPORTED_EXTENSIONS.get(ext) or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

    size = len(file_bytes)
    if size > 15 * 1024 * 1024:
        file_uri = upload_to_gemini_files_api(file_bytes, original_name, mime_type, api_key)
        file_part = {"file_data": {"mime_type": mime_type, "file_uri": file_uri}}
    else:
        encoded = base64.b64encode(file_bytes).decode("ascii")
        file_part = {"inline_data": {"mime_type": mime_type, "data": encoded}}

    user_prompt = (
        f"Original filename: {original_name}\n"
        f"Original extension: {original_suffix}\n"
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
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            return parsed["new_filename"]
        except Exception as e:
            logging.warning("Model %s failed for %s (%s), trying next fallback...", model, original_name, e)
            last_err = e

    assert last_err is not None
    raise last_err


def sanitize_filename(raw_name: str, original_suffix: str) -> str:
    name = raw_name.strip()
    mv_match = re.match(r"^mv\s+(?:-[a-zA-Z]+\s+)*(?:'[^']*'|\"[^\"]*\"|\S+)\s+['\"]?(.+?)['\"]?$", name)
    if mv_match:
        name = mv_match.group(1).strip()

    name = name.replace("'", "").replace('"', "").replace("`", "")
    name = name.replace("/", "-").replace(":", "-").replace("\x00", "")
    name = name.replace("_", " ")
    name = re.sub(r"\s+", " ", name).strip()

    if original_suffix:
        if not name.lower().endswith(original_suffix.lower()):
            stem, ext = os.path.splitext(name)
            if ext.lower() in SUPPORTED_EXTENSIONS:
                name = stem
            name = f"{name}{original_suffix}"

    if not re.match(r"^(\d{4}-\d{2}-\d{2}|yyyy-mm-dd)\b", name):
        name = f"yyyy-mm-dd {name}"

    return name


def unique_destination_path(dest_dir: Path, filename: str, current_path: Path) -> Path:
    candidate = dest_dir / filename
    if not candidate.exists() or candidate.resolve() == current_path.resolve():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists() or candidate.resolve() == current_path.resolve():
            return candidate
        counter += 1


def rename_file_in_place(filepath: Path, api_key: str, models: list[str]) -> bool:
    if not filepath.exists() or not filepath.is_file():
        logging.warning("File %s does not exist or is not a regular file, skipping.", filepath)
        return False

    if filepath.name.endswith(RENAMING_SUFFIX):
        original_name = filepath.name[: -len(RENAMING_SUFFIX)]
        original_path = filepath.with_name(original_name)
    else:
        original_name = filepath.name
        original_path = filepath

    original_suffix = Path(original_name).suffix

    # 1. Read file bytes BEFORE renaming to .renaming so cloud providers (Google Drive / iCloud)
    #    don't deadlock (Errno 11: Resource deadlock avoided) trying to hydrate a renamed file.
    try:
        file_bytes = read_file_bytes_safely(filepath)
    except Exception as e:
        logging.exception("Failed to read bytes from %s: %s", filepath, e)
        return False

    # 2. Now that bytes are in memory, rename to <original_name>.renaming for visual feedback
    if filepath.name.endswith(RENAMING_SUFFIX):
        working_path = filepath
    else:
        working_path = filepath.with_name(f"{filepath.name}{RENAMING_SUFFIX}")
        try:
            shutil.move(str(filepath), str(working_path))
        except Exception as e:
            logging.warning("Could not rename %s to .renaming (%s), continuing in-place...", filepath.name, e)
            working_path = filepath

    logging.info("Processing file: %s (as %s)", original_name, working_path.name)

    try:
        raw_new_name = generate_new_filename(file_bytes, original_name, original_suffix, api_key, models)
        clean_name = sanitize_filename(raw_new_name, original_suffix)
        target_path = unique_destination_path(working_path.parent, clean_name, working_path)

        shutil.move(str(working_path), str(target_path))
        logging.info("Renamed: '%s' -> '%s'", original_name, target_path.name)
        return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logging.error("Gemini API HTTP error for %s: %s - %s", original_name, e.code, err_body)
        restore_original(working_path, original_path)
        return False
    except Exception as e:
        logging.exception("Failed to process %s: %s", original_name, e)
        restore_original(working_path, original_path)
        return False


def restore_original(working_path: Path, original_path: Path) -> None:
    try:
        if working_path.exists() and working_path != original_path:
            target = unique_destination_path(original_path.parent, original_path.name, working_path)
            shutil.move(str(working_path), str(target))
            logging.info("Restored original filename: %s", target.name)
    except Exception as restore_err:
        logging.error("Failed to restore %s to %s: %s", working_path, original_path, restore_err)


def main() -> int:
    shutil.rmtree("/Users/erezbashan/Downloads/renaming-scans", ignore_errors=True)
    setup_logging()

    files = [Path(arg).expanduser().resolve() for arg in sys.argv[1:] if arg.strip()]
    if not files:
        logging.info("No files passed to Auto Rename.")
        return 0

    env = load_env()
    api_key = os.environ.get("GEMINI_API_KEY") or env.get("GEMINI_API_KEY")
    custom_model = os.environ.get("GEMINI_MODEL") or env.get("GEMINI_MODEL")
    models = [custom_model] + [m for m in DEFAULT_MODELS if m != custom_model] if custom_model else list(DEFAULT_MODELS)

    if not api_key:
        logging.error("GEMINI_API_KEY not found in environment or %s", ENV_FILE)
        return 1

    for filepath in files:
        rename_file_in_place(filepath, api_key, models)

    # Always exit 0 so Automator never shows the "The action 'Run Shell Script' encountered an error" dialog
    return 0


if __name__ == "__main__":
    sys.exit(main())
