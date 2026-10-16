# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import json
import sqlite3
from pathlib import Path

db_path = str(Path(__file__).resolve().parent / "app.db")

try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM entries WHERE payload LIKE '%933%'").fetchall()
    
    print(f"Found {len(rows)} records with 933:")
    for row in rows:
        payload = json.loads(row['payload'])
        if payload.get('amount') == 933.0:
            print(f"MATCHED ID: {row['id']} | User: {row['user_id']} | Title: {payload.get('title')}")
    
    conn.close()
except Exception as e:
    print(f"Error: {e}")
