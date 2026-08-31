"""One-shot: add returns table (order_id/customer_email FK'd to existing tables).
Idempotent. Run from repo root: python db/migrate_add_returns.py
"""
import sqlite3, shutil, datetime, sys

DB = "db/marketsphere.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

have = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "returns" in have:
    print("returns table already present — nothing to do")
    sys.exit(0)

shutil.copy(DB, f"{DB}.pre-returns-{datetime.datetime.now():%Y%m%d%H%M%S}")

conn.executescript("""
PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE returns (
    return_id       TEXT PRIMARY KEY,
    order_id        TEXT,
    customer_email  TEXT,
    sku             TEXT,
    reason          TEXT,
    status          TEXT,
    created_at      TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (customer_email) REFERENCES users(email)
);

COMMIT;
PRAGMA foreign_keys=ON;
""")
conn.commit()

print("returns:", conn.execute("SELECT count(*) FROM returns").fetchone()[0])
print("fk violations:", conn.execute("PRAGMA foreign_key_check").fetchall())
conn.close()
