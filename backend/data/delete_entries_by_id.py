# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import sqlite3
from pathlib import Path

db_path = str(Path(__file__).resolve().parent / "app.db")
ids_to_delete = ['f1dffd7708ae', '67dab0c9f87b', '175257575fb5']

try:
    conn = sqlite3.connect(db_path)
    cur = conn.execute(f"DELETE FROM entries WHERE id IN ({','.join(['?']*len(ids_to_delete))})", ids_to_delete)
    conn.commit()
    print(f"Successfully deleted {cur.rowcount} records by ID.")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
