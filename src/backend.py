# src/compass/backend.py
import sqlite3
from pathlib import Path

class MarketSphereBackend:
    def __init__(self, db_path: str | Path, session_email: str | None = None):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # so rows act like dicts
        self.session_email = session_email

    def get_user(self, email: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None

    def get_order(self, order_id: str, customer_email: str) -> dict | None:
        """Scoped lookup. Wrong owner and nonexistent both return None —
        callers must not be able to distinguish the two (no enumeration oracle)."""
        row = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND customer_email = ?",
            (order_id, customer_email),
        ).fetchone()
        if not row:
            return None
        order = dict(row)
        order.pop("customer_email", None)   # never surface PII to the model
        return order

    def get_product(self, sku: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM products WHERE sku = ?", (sku,)
        ).fetchone()
        return dict(row) if row else None

    def search_products(self, query: str) -> list[dict]:
        search_term = f'%{query}%'
        rows = self.conn.execute(
            "SELECT * FROM products WHERE name LIKE ? OR sku LIKE ? OR description LIKE ?",
            (search_term, search_term, search_term),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()