# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import sqlite3
from pathlib import Path

db_path = str(Path(__file__).resolve().parent / "app.db")
target_name = "济南舜山茶人食府"

try:
    conn = sqlite3.connect(db_path)
    cur = conn.execute("DELETE FROM entries WHERE payload LIKE ?", (f'%{target_name}%',))
    conn.commit()
    print(f"Successfully deleted {cur.rowcount} records.")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
