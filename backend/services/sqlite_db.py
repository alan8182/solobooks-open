"""Shared SQLite database path for SoloBooks persistence."""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
APP_DB_FILE = DATA_DIR / "app.db"
