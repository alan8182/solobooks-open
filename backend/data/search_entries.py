# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import json
import sqlite3
from pathlib import Path

db_path = str(Path(__file__).resolve().parent / "app.db")
target_name = "济南舜山茶人食府"

try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 模糊搜索 payload 中包含该名称的记录
    rows = conn.execute("SELECT * FROM entries WHERE payload LIKE ?", (f'%{target_name}%',)).fetchall()
    
    print(f"Found {len(rows)} matching records:")
    for row in rows:
        payload = json.loads(row['payload'])
        print(f"ID: {row['id']} | User: {row['user_id']} | Title: {payload.get('title')} | Amount: {payload.get('amount')}")
    
    conn.close()
except Exception as e:
    print(f"Error: {e}")
