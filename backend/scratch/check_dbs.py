import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
APP_DB = DATA_DIR / "app.db"


def check_table(query: str, title: str) -> None:
    if not APP_DB.exists():
        print(f"app.db NOT FOUND at {APP_DB}")
        return
    try:
        conn = sqlite3.connect(str(APP_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
        print(f"--- {title} ---")
        for row in rows:
            print(dict(row))
        if not rows:
            print("Empty.")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")


print("Checking app.db (entries, top 5):")
check_table("SELECT * FROM entries ORDER BY updated_at DESC LIMIT 5", "entries")

print("\nChecking app.db (fixed_assets, top 5):")
check_table("SELECT * FROM fixed_assets LIMIT 5", "fixed_assets")

print("\nChecking app.db (auth_users, top 5):")
check_table("SELECT * FROM auth_users LIMIT 5", "auth_users")
