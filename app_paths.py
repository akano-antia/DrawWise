from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "DrawWise"
APP_VERSION = "5.4.2"


def resource_root() -> Path:
    """Folder containing bundled read-only resources."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def user_root() -> Path:
    """Writable per-user application folder."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def reports_root() -> Path:
    if sys.platform.startswith("win"):
        return Path.home() / "Documents" / APP_NAME / "Reports"
    return user_root() / "reports"


def ensure_runtime_layout() -> Path:
    """Create writable data/backups/report folders and seed history on first run.

    Returns the runtime root expected by GameConfig.csv_path().
    """
    runtime = user_root()
    data_dir = runtime / "data"
    backup_dir = runtime / "backups"
    data_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    reports_root().mkdir(parents=True, exist_ok=True)

    seed_dir = resource_root() / "data"
    if seed_dir.exists():
        for source in seed_dir.glob("*.csv"):
            destination = data_dir / source.name
            if not destination.exists():
                shutil.copy2(source, destination)
    return runtime
