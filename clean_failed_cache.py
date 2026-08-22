import sqlite3
from pathlib import Path

db_path = Path("output/catalog.db")
if db_path.exists():
    conn = sqlite3.connect(db_path)
    cur = conn.execute("DELETE FROM enriched_products WHERE confidence = 'LOW' OR sources_json = '[]'")
    print(f"Deleted {cur.rowcount} failed low-confidence records.")
    conn.commit()
    conn.close()
else:
    print("Database does not exist.")
