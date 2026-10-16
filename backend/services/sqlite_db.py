# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

"""Shared SQLite database path for SoloBooks persistence."""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
APP_DB_FILE = DATA_DIR / "app.db"
