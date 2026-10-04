from core.user_paths import get_user_data_dir
# actions/desktop_organizer_mcp.py
"""
Smart Desktop & Downloads Organizer MCP for NIGHTFALL AI.
Provides safe, intelligent file classification, dry-run previews,
full transaction rollback (undo), duplicate detection, and cleanup.
"""

import os
import sys
import json
import shutil
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional

logger = logging.getLogger("SmartOrganizerMCP")

# ── Paths & Config ──────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = get_user_data_dir() / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = CONFIG_DIR / "organizer_history.json"

# Protected names/extensions that should NEVER be moved automatically
PROTECTED_EXTENSIONS = {".lnk", ".url", ".sys", ".dll"}
PROTECTED_FILENAMES = {
    "desktop.ini", "thumbs.db", ".ds_store",
    "NIGHTFALL_history.json", "organizer_history.json",
    "email_credentials.json", ".email_key", "api_keys.json"
}
PROTECTED_DIR_PREFIXES = (".", "NIGHTFALLprojects", ".NIGHTFALL", ".git", ".venv", "node_modules", "$recycle.bin")

# ── Categorization Rules ───────────────────────────────────────────────────

CATEGORY_MAP = {
    "Documents": [
        ".pdf", ".docx", ".doc", ".txt", ".rtf", ".odt",
        ".pptx", ".ppt", ".xlsx", ".xls", ".csv", ".tsv",
        ".epub", ".mobi", ".pages", ".numbers", ".key"
    ],
    "Installers": [
        ".exe", ".msi", ".dmg", ".pkg", ".iso", ".deb", ".rpm",
        ".appx", ".msix", ".apk"
    ],
    "Archives": [
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"
    ],
    "Images": [
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg",
        ".ico", ".tiff", ".tif", ".psd", ".ai", ".raw", ".cr2", ".nef"
    ],
    "Code & Dev": [
        ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
        ".json", ".yaml", ".yml", ".xml", ".sql", ".sh", ".ps1",
        ".bat", ".cpp", ".c", ".h", ".hpp", ".cs", ".java",
        ".rs", ".go", ".php", ".rb", ".ipynb", ".md"
    ],
    "Audio": [
        ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".aiff"
    ],
    "Videos": [
        ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v"
    ],
    "Datasets": [
        ".parquet", ".arrow", ".feather", ".h5", ".hdf5", ".sqlite", ".db"
    ]
}


def _resolve_target_dir(target: str) -> Path:
    """Resolves standard target names to local system paths."""
    cleaned = (target or "").strip().lower()
    home = Path.home()
    if cleaned in ("desktop", "screen", "desk"):
        return home / "Desktop"
    elif cleaned in ("downloads", "download", "dl"):
        return home / "Downloads"
    elif cleaned in ("documents", "docs", "doc"):
        return home / "Documents"
    elif cleaned in ("pictures", "photos", "images"):
        return home / "Pictures"
    elif cleaned in ("music", "audio"):
        return home / "Music"
    elif cleaned in ("videos", "movies"):
        return home / "Videos"
    
    # Try custom path
    p = Path(target).expanduser().resolve()
    if p.exists() and p.is_dir():
        return p
    # Fallback to Downloads if target invalid
    return home / "Downloads"


def _format_bytes(size: int) -> str:
    """Formats file size into human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _is_screenshot(filename: str, ext: str) -> bool:
    """Detects whether an image file is likely a screenshot."""
    if ext.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return False
    lower = filename.lower()
    indicators = [
        "screenshot", "screen shot", "prntscr", "snip", "capture",
        "shot_", "scrn_", "screenshot_", "image_"
    ]
    return any(ind in lower for ind in indicators)


def _get_category(file_path: Path) -> str:
    """Determines destination category folder for a given file."""
    ext = file_path.suffix.lower()
    name = file_path.name
    
    # Dedicated subfolder for screenshots
    if _is_screenshot(name, ext):
        return "Images/Screenshots"
    
    for cat, extensions in CATEGORY_MAP.items():
        if ext in extensions:
            return cat
    return "Others"


def _compute_partial_hash(file_path: Path, chunk_size: int = 65536) -> str:
    """Computes a fast SHA-256 checksum based on file size and first/last 64KB."""
    try:
        size = file_path.stat().st_size
        if size == 0:
            return "empty_file"
        hasher = hashlib.sha256()
        hasher.update(str(size).encode())
        with open(file_path, "rb") as f:
            hasher.update(f.read(chunk_size))
            if size > chunk_size * 2:
                f.seek(size - chunk_size)
                hasher.update(f.read(chunk_size))
        return hasher.hexdigest()
    except Exception:
        return ""


def _is_protected_item(item: Path) -> bool:
    """Checks if an item should be excluded from automatic organization."""
    lower_name = item.name.lower()
    if lower_name.startswith("."):
        return True
    if lower_name in PROTECTED_FILENAMES:
        return True
    if item.suffix.lower() in PROTECTED_EXTENSIONS:
        return True
    if any(lower_name.startswith(p) for p in PROTECTED_DIR_PREFIXES):
        return True
    return False


# ── SmartOrganizerEngine ────────────────────────────────────────────────────

class SmartOrganizerEngine:
    """Core organizer engine supporting dry-run, undo, duplicates, and archiving."""

    def __init__(self):
        self.history_file = HISTORY_FILE

    def _load_history(self) -> List[Dict[str, Any]]:
        if not self.history_file.exists():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load history: {e}")
            return []

    def _save_history(self, history: List[Dict[str, Any]]) -> None:
        try:
            # Keep at most 20 history runs
            history = history[-20:]
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save history: {e}")

    def preview(self, target: str = "downloads", mode: str = "by_type") -> str:
        """Dry-run preview of planned file moves without touching any files."""
        dir_path = _resolve_target_dir(target)
        if not dir_path.exists():
            return f"❌ Target directory not found: {dir_path}"

        plan: Dict[str, List[Tuple[str, int]]] = {}
        total_files = 0
        total_bytes = 0

        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                continue
            if _is_protected_item(item):
                continue

            size = item.stat().st_size
            total_files += 1
            total_bytes += size

            if mode == "by_date":
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                dest = f"By_Date/{mtime.strftime('%Y-%m')}"
            else:
                dest = _get_category(item)

            if dest not in plan:
                plan[dest] = []
            plan[dest].append((item.name, size))

        if total_files == 0:
            return f"✨ **{dir_path.name}** is already clean and organized! No files need moving."

        lines = [
            f"🔍 **Preview of Organization for `{dir_path.name}`** ({total_files} files, {_format_bytes(total_bytes)} total):\n"
        ]
        for dest, files in sorted(plan.items(), key=lambda x: len(x[1]), reverse=True):
            sub_size = sum(s for _, s in files)
            lines.append(f"📁 **{dest}/** ({len(files)} files, {_format_bytes(sub_size)}):")
            for fname, s in files[:4]:
                lines.append(f"   • {fname} ({_format_bytes(s)})")
            if len(files) > 4:
                lines.append(f"   • *... and {len(files) - 4} more files*")
            lines.append("")

        lines.append("💡 *No files have been moved yet. Say 'Organize my downloads' or execute with action='organize' to apply.*")
        return "\n".join(lines)

    def organize(self, target: str = "downloads", mode: str = "by_type") -> str:
        """Executes intelligent file reorganization and logs reversible transactions."""
        dir_path = _resolve_target_dir(target)
        if not dir_path.exists():
            return f"❌ Target directory not found: {dir_path}"

        moves_recorded: List[Dict[str, str]] = []
        moved_count = 0
        skipped_count = 0
        summary_by_cat: Dict[str, int] = {}

        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                continue
            if _is_protected_item(item):
                continue

            if mode == "by_date":
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                rel_dest = f"By_Date/{mtime.strftime('%Y-%m')}"
            else:
                rel_dest = _get_category(item)

            dest_folder = dir_path / rel_dest
            dest_folder.mkdir(parents=True, exist_ok=True)
            dest_file = dest_folder / item.name

            # Collision avoidance
            if dest_file.exists():
                stem = item.stem
                suffix = item.suffix
                counter = 1
                while dest_file.exists():
                    dest_file = dest_folder / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                original_path_str = str(item.resolve())
                new_path_str = str(dest_file.resolve())
                shutil.move(str(item), str(dest_file))
                moves_recorded.append({
                    "original": original_path_str,
                    "current": new_path_str
                })
                moved_count += 1
                summary_by_cat[rel_dest] = summary_by_cat.get(rel_dest, 0) + 1
            except Exception as e:
                logger.error(f"Failed to move {item.name}: {e}")
                skipped_count += 1

        if moved_count == 0:
            return f"✨ **{dir_path.name}** has no loose files to organize."

        # Save to transaction history for undo
        history = self._load_history()
        history.append({
            "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "timestamp": datetime.now().isoformat(),
            "target": str(dir_path),
            "target_name": dir_path.name,
            "count": moved_count,
            "moves": moves_recorded
        })
        self._save_history(history)

        try:
            from core.undo import push_undo
            push_undo(f"Organize {dir_path.name} ({moved_count} files)", lambda: self.undo())
        except Exception:
            pass

        lines = [
            f"✅ **Successfully organized {moved_count} file(s) in `{dir_path.name}`!**\n",
            "**Categories Created:**"
        ]
        for cat, count in sorted(summary_by_cat.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  • **{cat}/**: {count} file(s)")
        
        if skipped_count > 0:
            lines.append(f"\n⚠️ *{skipped_count} file(s) could not be moved due to permissions.*")
            
        lines.append(f"\n↩️ *All changes are reversible! Run `action='undo'` or say 'Undo last organization' to rollback.*")
        return "\n".join(lines)

    def undo(self) -> str:
        """Rolls back the most recent organization transaction."""
        history = self._load_history()
        if not history:
            return "ℹ️ No previous organization transactions found to undo."

        last_run = history.pop()
        moves = last_run.get("moves", [])
        target_name = last_run.get("target_name", "folder")
        restored = 0
        failed = 0

        for m in reversed(moves):
            orig = Path(m["original"])
            curr = Path(m["current"])
            if curr.exists():
                try:
                    orig.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(curr), str(orig))
                    restored += 1
                except Exception as e:
                    logger.error(f"Could not restore {curr} -> {orig}: {e}")
                    failed += 1
            else:
                failed += 1

        self._save_history(history)

        res = f"↩️ **Undo Complete**: Restored **{restored}** file(s) back to `{target_name}` root."
        if failed > 0:
            res += f" ({failed} files could not be restored because they were moved or deleted externally)."
        return res

    def find_duplicates(self, target: str = "downloads") -> str:
        """Finds duplicate files based on content checksums and copy-pattern names."""
        dir_path = _resolve_target_dir(target)
        if not dir_path.exists():
            return f"❌ Target directory not found: {dir_path}"

        seen_hashes: Dict[str, List[Tuple[Path, int]]] = {}
        pattern_duplicates: List[Path] = []
        zero_byte_files: List[Path] = []

        base_depth = len(dir_path.resolve().parts)
        for root, dirs, files in os.walk(dir_path):
            # Prune protected/hidden directories in-place so os.walk does not recurse into them
            dirs[:] = [d for d in dirs if not any(d.lower().startswith(p) for p in PROTECTED_DIR_PREFIXES)]
            
            # Limit scan depth to 3 levels to maintain fast, responsive execution
            current_depth = len(Path(root).parts) - base_depth
            if current_depth >= 3:
                dirs.clear()

            for f in files:
                p = Path(root) / f
                if _is_protected_item(p):
                    continue
                try:
                    size = p.stat().st_size
                    if size == 0:
                        zero_byte_files.append(p)
                        continue
                    
                    # Pattern check e.g. "file (1).pdf", "image_copy.png"
                    stem = p.stem.lower()
                    if (" (1)" in stem or " (2)" in stem or "_copy" in stem or "- copy" in stem):
                        pattern_duplicates.append(p)

                    # Content checksum check
                    h = _compute_partial_hash(p)
                    if h:
                        if h not in seen_hashes:
                            seen_hashes[h] = []
                        seen_hashes[h].append((p, size))
                except Exception:
                    continue

        exact_dupes = {h: files for h, files in seen_hashes.items() if len(files) > 1}

        if not exact_dupes and not pattern_duplicates and not zero_byte_files:
            return f"✨ **No duplicate files detected in `{dir_path.name}`!**"

        lines = [f"🔍 **Duplicate & Redundant Files Report for `{dir_path.name}`:**\n"]

        total_wasted = 0
        if exact_dupes:
            lines.append(f"**Identical Content Duplicates ({len(exact_dupes)} sets):**")
            for h, flist in list(exact_dupes.items())[:5]:
                orig = flist[0][0]
                size = flist[0][1]
                wasted = size * (len(flist) - 1)
                total_wasted += wasted
                lines.append(f"  • {_format_bytes(size)} duplicate set (wasting {_format_bytes(wasted)}):")
                for fpath, _ in flist:
                    lines.append(f"    - `{fpath.relative_to(dir_path)}`")
            if len(exact_dupes) > 5:
                lines.append(f"  *... and {len(exact_dupes) - 5} more duplicate sets.*")
            lines.append("")

        if pattern_duplicates:
            lines.append(f"**Copy-pattern Files (`(1)`, `copy`) ({len(pattern_duplicates)} files):**")
            for p in pattern_duplicates[:5]:
                lines.append(f"  • `{p.name}` ({_format_bytes(p.stat().st_size)})")
            if len(pattern_duplicates) > 5:
                lines.append(f"  *... and {len(pattern_duplicates) - 5} more.*")
            lines.append("")

        if zero_byte_files:
            lines.append(f"**Zero-Byte (Empty) Files ({len(zero_byte_files)} files):**")
            for p in zero_byte_files[:5]:
                lines.append(f"  • `{p.name}`")
            lines.append("")

        if total_wasted > 0:
            lines.append(f"💾 **Estimated reclaimable disk space:** {_format_bytes(total_wasted)}")

        return "\n".join(lines)

    def clean_empty_folders(self, target: str = "downloads") -> str:
        """Removes empty directories left behind safely."""
        dir_path = _resolve_target_dir(target)
        if not dir_path.exists():
            return f"❌ Target directory not found: {dir_path}"

        removed = []
        for root, dirs, files in os.walk(dir_path, topdown=False):
            for d in dirs:
                full_d = Path(root) / d
                if _is_protected_item(full_d):
                    continue
                try:
                    if not any(full_d.iterdir()):
                        full_d.rmdir()
                        removed.append(full_d.name)
                except Exception:
                    pass

        if not removed:
            return f"✨ No empty folders found in `{dir_path.name}`."
        return f"🧹 Removed {len(removed)} empty folder(s) in `{dir_path.name}`: {', '.join(removed[:8])}."

    def archive_old(self, target: str = "downloads", days: int = 30) -> str:
        """Moves files older than `days` into an Archive/YYYY-MM folder."""
        dir_path = _resolve_target_dir(target)
        if not dir_path.exists():
            return f"❌ Target directory not found: {dir_path}"

        cutoff = datetime.now() - timedelta(days=days)
        archived_count = 0
        moves_recorded = []

        for item in sorted(dir_path.iterdir()):
            if item.is_dir() or _is_protected_item(item):
                continue

            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime < cutoff:
                    arch_folder = dir_path / "Archive" / mtime.strftime("%Y-%m")
                    arch_folder.mkdir(parents=True, exist_ok=True)
                    dest_file = arch_folder / item.name
                    if dest_file.exists():
                        dest_file = arch_folder / f"{item.stem}_{int(datetime.now().timestamp())}{item.suffix}"
                    
                    orig_str = str(item.resolve())
                    curr_str = str(dest_file.resolve())
                    shutil.move(str(item), str(dest_file))
                    moves_recorded.append({"original": orig_str, "current": curr_str})
                    archived_count += 1
            except Exception as e:
                logger.error(f"Archive error for {item.name}: {e}")

        if archived_count == 0:
            return f"ℹ️ No files older than {days} days found in `{dir_path.name}`."

        history = self._load_history()
        history.append({
            "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "timestamp": datetime.now().isoformat(),
            "target": str(dir_path),
            "target_name": dir_path.name,
            "count": archived_count,
            "moves": moves_recorded
        })
        self._save_history(history)

        try:
            from core.undo import push_undo
            push_undo(f"Archive {dir_path.name} ({archived_count} files)", lambda: self.undo())
        except Exception:
            pass

        return f"📦 **Archived {archived_count} files** older than {days} days into `{dir_path.name}/Archive/`. (Reversible via undo)"


# ── Unified MCP Dispatcher ──────────────────────────────────────────────────

_engine_instance: Optional[SmartOrganizerEngine] = None


def get_organizer_engine() -> SmartOrganizerEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = SmartOrganizerEngine()
    return _engine_instance


def smart_organizer(
    parameters: Optional[Dict[str, Any]] = None,
    player=None,
    speak=None,
) -> str:
    """
    Unified entry point for Smart Desktop & Downloads Organizer MCP.
    Supported actions: 'preview', 'organize', 'undo', 'find_duplicates', 'clean_empty_folders', 'archive_old'.
    """
    p = parameters or {}
    action = (p.get("action") or "preview").strip().lower()
    target = p.get("target") or "downloads"
    mode = p.get("mode") or "by_type"
    days = int(p.get("days") or 30)

    engine = get_organizer_engine()

    try:
        if action in ("preview", "dry_run", "dryrun", "inspect"):
            res = engine.preview(target=target, mode=mode)
        elif action in ("organize", "clean", "sort", "categorize"):
            res = engine.organize(target=target, mode=mode)
        elif action in ("undo", "rollback", "revert"):
            res = engine.undo()
        elif action in ("find_duplicates", "duplicates", "dupes"):
            res = engine.find_duplicates(target=target)
        elif action in ("clean_empty_folders", "clean_empty", "remove_empty"):
            res = engine.clean_empty_folders(target=target)
        elif action in ("archive_old", "archive"):
            res = engine.archive_old(target=target, days=days)
        else:
            res = f"Unknown organizer action: '{action}'. Available: preview, organize, undo, find_duplicates, clean_empty_folders, archive_old."

        if player and hasattr(player, "write_log"):
            first_line = res.split("\n")[0].replace("*", "").replace("`", "")
            player.write_log(f"[SmartOrganizer] {first_line}")

        return res

    except Exception as exc:
        logger.exception(f"Smart organizer error: {exc}")
        err_msg = f"Error organizing files: {exc}"
        if speak:
            speak("There was an issue organizing the folder, sir.")
        return err_msg
