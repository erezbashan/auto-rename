# Renaming Scans

Automatically renames scanned documents and images dropped into `~/Downloads/rename` using the Gemini API and moves them back to `~/Downloads`.

## How It Works

1. Drop any `.pdf`, `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, or `.txt` file into `~/Downloads/rename`.
2. macOS `launchd` detects the change via `WatchPaths` and triggers `rename_scans.py`.
3. `rename_scans.py` sends the file to Gemini (`gemini-3.5-flash` with fallback to `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite`) with your Gem's instructions:
   - Starts with `yyyy-mm-dd` extracted from the document (or literal `yyyy-mm-dd` if no date is found).
   - Concise description in the same language as the document.
   - Spaces separating words (no underscores, no quotes).
   - Preserves the original file extension.
4. Moves the renamed file to `~/Downloads/<new name>` and shows a macOS notification.
5. If an error occurs, the file is moved to `~/Downloads/rename/_failed/` so it doesn't loop indefinitely.

## Files

- `rename_scans.py`: Main watcher/processor script.
- `com.erezbashan.rename-scans.plist`: macOS LaunchAgent configuration (installed in `~/Library/LaunchAgents/`).
- `.env`: Contains `GEMINI_API_KEY` (and optional `GEMINI_MODEL`).
- `logs/renamer.log`: Execution and rename history log.

## Managing the Background Service

- **Check status:**
  ```bash
  launchctl list | grep rename-scans
  ```
- **View logs:**
  ```bash
  tail -f ~/workspace/renaming-scans/logs/renamer.log
  ```
- **Restart / reload service:**
  ```bash
  launchctl unload ~/Library/LaunchAgents/com.erezbashan.rename-scans.plist
  launchctl load ~/Library/LaunchAgents/com.erezbashan.rename-scans.plist
  ```
- **Run manually:**
  ```bash
  /opt/homebrew/bin/python3 ~/workspace/renaming-scans/rename_scans.py
  ```
