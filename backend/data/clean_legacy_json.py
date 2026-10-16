# Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
# Licensed under the SoloBooks License v2.0.

import json
import os

json_path = r'\\阿兰\f\solobooks\backend\data\entries.json'
target_name = "济南舜山茶人食府"

if os.path.exists(json_path):
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, dict) and 'entries' in data:
            old_count = len(data['entries'])
            data['entries'] = [e for e in data['entries'] if target_name not in str(e)]
            new_count = len(data['entries'])
            
            if old_count != new_count:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"Removed {old_count - new_count} records from entries.json")
            else:
                print("No matching records found in entries.json")
    except Exception as e:
        print(f"Error processing JSON: {e}")
else:
    print("entries.json not found.")
