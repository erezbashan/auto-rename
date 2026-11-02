# Auto Rename

macOS Finder Quick Action (`Auto Rename`) that renames one or more selected documents/images in-place using the Gemini API according to your Gem's rules.

## How to Use

1. In **Finder**, select one or multiple files (`.pdf`, `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, `.txt`) in any folder.
2. **Right-click** the selection → **Quick Actions** → **Auto Rename**.
3. Each selected file is immediately renamed to `<original name>.renaming` while Gemini (`gemini-3.5-flash` with fallback to `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite`) analyzes its contents, and is then renamed **in-place** in the same folder:
   - Starts with `yyyy-mm-dd` extracted from the document (or literal `yyyy-mm-dd` if no date is found).
   - Concise description in the same language as the document.
   - Spaces separating words (no underscores, no quotes).
   - Preserves the original file extension.
4. If an error occurs, the file is automatically restored from `<original name>.renaming` back to `<original name>`.

## Project Structure

- `auto_rename.py`: Core script that accepts one or more file paths and renames them in-place.
- `Auto Rename.workflow`: macOS Automator Quick Action bundle (installed into `~/Library/Services/Auto Rename.workflow`).
- `.env`: Contains `GEMINI_API_KEY` (and optional `GEMINI_MODEL`).
- `logs/renamer.log`: Execution and rename history log.

## Re-installing the Quick Action or Running Manually

- **Re-install Quick Action:**
  ```bash
  cp -R ~/workspace/auto-rename/"Auto Rename.workflow" ~/Library/Services/
  /System/Library/CoreServices/pbs -update
  ```
- **Run from command line on one or more files:**
  ```bash
  /opt/homebrew/bin/python3 ~/workspace/auto-rename/auto_rename.py /path/to/file1.pdf /path/to/file2.pdf
  ```
- **View logs:**
  ```bash
  tail -f ~/workspace/auto-rename/logs/renamer.log
  ```
