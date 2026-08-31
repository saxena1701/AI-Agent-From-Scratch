# src/compass/backend.py
import sqlite3
from datetime import datetime
from pathlib import Path

CANCELLABLE_STATUSES = {"processing"}
RETURN_WINDOW_DAYS = 30

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

    def _get_owned_order(self, order_id: str, customer_email: str) -> dict | None:
        """Scoped lookup shared by every order-touching method. Wrong owner and
        nonexistent both return None — callers must not be able to distinguish
        the two (no enumeration oracle). customer_email is NOT stripped here;
        callers that surface results to the model must strip it themselves."""
        row = self.conn.execute(
            "SELECT * FROM orders WHERE order_id = ? AND customer_email = ?",
            (order_id, customer_email),
        ).fetchone()
        return dict(row) if row else None

    def get_order(self, order_id: str, customer_email: str) -> dict | None:
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        order.pop("customer_email", None)   # never surface PII to the model
        return order

    def list_orders(self, customer_email: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM orders WHERE customer_email = ? ORDER BY order_date DESC",
            (customer_email,),
        ).fetchall()
        orders = [dict(r) for r in rows]
        for o in orders:
            o.pop("customer_email", None)
        return orders

    def cancel_order(self, order_id: str, customer_email: str) -> dict | None:
        """None = not found or not owned (indistinguishable, see _get_owned_order).
        {"error": "not_cancellable", "status": ...} = found, owned, wrong status.
        Otherwise: the updated order dict, status now "cancelled"."""
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        if order["status"] not in CANCELLABLE_STATUSES:
            return {"error": "not_cancellable", "status": order["status"]}
        self.conn.execute(
            "UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order_id,)
        )
        self.conn.commit()
        order["status"] = "cancelled"
        order.pop("customer_email", None)
        return order

    def check_return_eligibility(self, order_id: str, customer_email: str) -> dict | None:
        """None = not found or not owned. Otherwise {"eligible": bool, ...detail}."""
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        if order["status"] != "delivered":
            return {"eligible": False, "reason": "not_delivered", "status": order["status"]}
        order_date = datetime.fromisoformat(order["order_date"])
        days_since = (datetime.now() - order_date).days
        if days_since > RETURN_WINDOW_DAYS:
            return {"eligible": False, "reason": "window_expired", "days_since_order": days_since}
        return {"eligible": True, "days_since_order": days_since}

    def initiate_return(self, order_id: str, customer_email: str, reason: str | None = None) -> dict | None:
        """None = not found or not owned. {"error": "not_eligible", ...} = ineligible
        (same shape as check_return_eligibility's ineligible detail). Otherwise the
        created return record."""
        order = self._get_owned_order(order_id, customer_email)
        if not order:
            return None
        eligibility = self.check_return_eligibility(order_id, customer_email)
        if not eligibility["eligible"]:
            return {"error": "not_eligible", **{k: v for k, v in eligibility.items() if k != "eligible"}}

        count = self.conn.execute("SELECT COUNT(*) FROM returns").fetchone()[0]
        return_id = f"RET-{100001 + count}"
        created_at = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO returns (return_id, order_id, customer_email, sku, reason, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'requested', ?)",
            (return_id, order_id, customer_email, order["sku"], reason, created_at),
        )
        self.conn.commit()
        return {
            "return_id": return_id,
            "order_id": order_id,
            "sku": order["sku"],
            "reason": reason,
            "status": "requested",
            "created_at": created_at,
        }

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