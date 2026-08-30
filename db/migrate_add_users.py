"""One-shot: add users table, FK orders.customer_email -> users.email.
Idempotent. Run from repo root: python db/migrate_add_users.py
"""
import sqlite3, shutil, datetime, sys

DB = "db/marketsphere.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

have = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "users" in have:
    print("users table already present — nothing to do")
    sys.exit(0)

shutil.copy(DB, f"{DB}.pre-users-{datetime.datetime.now():%Y%m%d%H%M%S}")

conn.executescript("""
PRAGMA foreign_keys=OFF;
BEGIN;

CREATE TABLE users (
    email      TEXT PRIMARY KEY,
    name       TEXT,
    created_at TEXT
);

INSERT INTO users (email, name, created_at)
SELECT DISTINCT customer_email,
       'Customer ' || substr(customer_email, 9, 1),
       datetime('now')
FROM orders WHERE customer_email IS NOT NULL;

-- rebuild orders to add FK (sqlite cannot ALTER a constraint in)
CREATE TABLE orders_new (
    order_id        TEXT PRIMARY KEY,
    customer_email  TEXT,
    sku             TEXT,
    quantity        INTEGER,
    order_date      TEXT,
    status          TEXT,
    tracking_number TEXT,
    FOREIGN KEY (sku) REFERENCES products(sku),
    FOREIGN KEY (customer_email) REFERENCES users(email)
);
INSERT INTO orders_new SELECT * FROM orders;
DROP TABLE orders;
ALTER TABLE orders_new RENAME TO orders;

COMMIT;
PRAGMA foreign_keys=ON;
""")
conn.commit()

print("users:", conn.execute("SELECT count(*) FROM users").fetchone()[0])
print("orders:", conn.execute("SELECT count(*) FROM orders").fetchone()[0])
print("fk violations:", conn.execute("PRAGMA foreign_key_check").fetchall())
conn.close()
