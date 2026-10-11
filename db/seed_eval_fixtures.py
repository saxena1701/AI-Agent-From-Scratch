"""One-shot: add eval fixture data (users/orders, one out-of-stock product) so the eval
set in eval/ has scenarios the original seed data couldn't cover — most importantly a
delivered order still inside the 30-day return window (RETURN_WINDOW_DAYS in backend.py).
Idempotent (INSERT OR IGNORE on primary keys). Run from repo root: python db/seed_eval_fixtures.py

Order dates are computed relative to "now" at the moment this script runs, so the
eligible/expired split is only guaranteed to match eval/eval_set.json's ground truth on
the day it was generated. Re-run this script to refresh the fixtures (existing rows are
left alone; see eval/README.md) if the eligible/expired examples have drifted.
"""
import sqlite3
import shutil
import datetime

DB = "db/marketsphere.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

marker_users = {r["email"] for r in conn.execute("SELECT email FROM users")}
did_anything = False

if "customer7@example.com" not in marker_users:
    shutil.copy(DB, f"{DB}.pre-eval-fixtures-{datetime.datetime.now():%Y%m%d%H%M%S}")
    now = datetime.datetime.now()

    def days_ago(n: int) -> str:
        return (now - datetime.timedelta(days=n)).isoformat(timespec="seconds")

    users = [(f"customer{i}@example.com", f"Customer {i}", now.isoformat(timespec="seconds")) for i in range(7, 13)]

    orders = [
        # order_id,      customer_email,          sku,             qty, order_date,     status,        tracking_number
        ("ORD-200001", "customer7@example.com",  "MS-HEAD-1002", 1, days_ago(5),  "delivered",  "TRACK-200001"),
        ("ORD-200002", "customer8@example.com",  "MS-KITC-2003", 1, days_ago(10), "delivered",  "TRACK-200002"),
        ("ORD-200003", "customer9@example.com",  "MS-FIT-3004",  2, days_ago(29), "delivered",  "TRACK-200003"),
        ("ORD-200004", "customer10@example.com", "MS-HOME-4002", 1, days_ago(31), "delivered",  "TRACK-200004"),
        ("ORD-200005", "customer11@example.com", "MS-OUT-5003",  1, days_ago(3),  "processing", "TRACK-200005"),
        ("ORD-200006", "customer12@example.com", "MS-APP-6004",  1, days_ago(2),  "shipped",    "TRACK-200006"),
        ("ORD-200007", "customer7@example.com",  "MS-LAPTOP-002", 1, days_ago(60), "delivered", "TRACK-200007"),
        ("ORD-200008", "customer9@example.com",  "MS-SOFA-001",  1, days_ago(1),  "processing", "TRACK-200008"),
    ]

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executemany("INSERT OR IGNORE INTO users (email, name, created_at) VALUES (?, ?, ?)", users)
    conn.executemany(
        "INSERT OR IGNORE INTO orders (order_id, customer_email, sku, quantity, order_date, status, tracking_number) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        orders,
    )
    conn.execute("UPDATE products SET in_stock = 0 WHERE sku = 'MS-HEAD-1004'")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    did_anything = True
else:
    print("batch 1 eval fixtures (customer7-12) already present — skipping")

if "customer13@example.com" not in marker_users:
    now = datetime.datetime.now()

    def days_ago(n: int) -> str:
        return (now - datetime.timedelta(days=n)).isoformat(timespec="seconds")

    users2 = [(f"customer{i}@example.com", f"Customer {i}", now.isoformat(timespec="seconds")) for i in range(13, 21)]

    orders2 = [
        # order_id,      customer_email,          sku,             qty, order_date,     status,        tracking_number
        ("ORD-300001", "customer13@example.com", "MS-KITC-2005", 1, days_ago(3),  "delivered",  "TRACK-300001"),
        ("ORD-300002", "customer14@example.com", "MS-OUT-5004",  1, days_ago(45), "delivered",  "TRACK-300002"),
        ("ORD-300003", "customer15@example.com", "MS-HOME-4005", 1, days_ago(1),  "processing", "TRACK-300003"),
        ("ORD-300004", "customer16@example.com", "MS-APP-6005",  1, days_ago(4),  "shipped",    "TRACK-300004"),
        ("ORD-300005", "customer17@example.com", "MS-FIT-3002",  1, days_ago(20), "delivered",  "TRACK-300005"),
        ("ORD-300006", "customer13@example.com", "MS-HEAD-1006", 1, days_ago(10), "delivered",  "TRACK-300006"),
        ("ORD-300007", "customer18@example.com", "MS-OUT-5006",  1, days_ago(25), "cancelled",  "TRACK-300007"),
        ("ORD-300008", "customer19@example.com", "MS-HOME-4007", 1, days_ago(2),  "delivered",  "TRACK-300008"),
        ("ORD-300009", "customer20@example.com", "MS-KITC-2009", 1, days_ago(1),  "processing", "TRACK-300009"),
        ("ORD-300010", "customer14@example.com", "MS-FIT-3005",  1, days_ago(6),  "shipped",    "TRACK-300010"),
        ("ORD-300011", "customer15@example.com", "MS-APP-6002",  1, days_ago(35), "delivered",  "TRACK-300011"),
        ("ORD-300012", "customer16@example.com", "MS-OUT-5008",  1, days_ago(15), "delivered",  "TRACK-300012"),
    ]

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executemany("INSERT OR IGNORE INTO users (email, name, created_at) VALUES (?, ?, ?)", users2)
    conn.executemany(
        "INSERT OR IGNORE INTO orders (order_id, customer_email, sku, quantity, order_date, status, tracking_number) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        orders2,
    )
    conn.execute("UPDATE products SET in_stock = 0 WHERE sku = 'MS-OUT-5001'")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    did_anything = True
else:
    print("batch 2 eval fixtures (customer13-20) already present — skipping")

if not did_anything:
    print("eval fixtures already present — nothing to do")

print("users:", conn.execute("SELECT count(*) FROM users").fetchone()[0])
print("orders:", conn.execute("SELECT count(*) FROM orders").fetchone()[0])
print("out-of-stock products:", [r["sku"] for r in conn.execute("SELECT sku FROM products WHERE in_stock = 0")])
print("fk violations:", conn.execute("PRAGMA foreign_key_check").fetchall())
conn.close()
