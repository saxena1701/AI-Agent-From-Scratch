# Seed `products` Table from RAG Knowledge Base Implementation Plan

## Overview

`db/marketsphere.db`'s `products` table currently has 5 rows, but the RAG knowledge base at `Building-RAG-from-scratch/data/knowledge_base` contains 51 product manuals across 6 categories that the `retrieve` tool can surface. When the agent retrieves a manual chunk about, say, the "ChefPro Stand Mixer 5Qt," `lookup_product`/`get_product_details` currently returns nothing for it. This plan adds the 51 missing products (plus a few sample orders) so the structured-lookup tools and the RAG `retrieve` tool are backed by a consistent catalog.

## Current State Analysis

- `db/marketsphere.db` schema:
  - `products(sku TEXT PRIMARY KEY, name TEXT, category TEXT, price REAL, description TEXT, in_stock INTEGER)`
  - `orders(order_id TEXT PRIMARY KEY, customer_email TEXT, sku TEXT, quantity INTEGER, order_date TEXT, status TEXT, tracking_number TEXT, FOREIGN KEY (sku) REFERENCES products(sku))`
- Existing 5 products use SKUs like `MS-LAPTOP-001`, categories `electronics`/`apparel`/`home_goods`, and one-line descriptions (`"13-inch FHD"`, `"256GB"`, `"Grey fabric"`).
- Existing 2 orders use `order_id` format `ORD-10000N`, `customer_email` format `customerN@example.com`, `tracking_number` format `TRACK-XXXXXX`, and free-text `status` (no enum/constraint anywhere in `backend.py`/`tools.py`) — currently `delivered` and `shipped`.
- The knowledge base's `marketsphere_kb_meta/manifest.jsonl` lists 51 manual PDFs (`source_type: manuals`) with `document_id` (e.g. `ms-head-1001`) and `title` (e.g. `"AeroPulse Wireless Over-Ear Headphones - User Manual"`). Confirmed by reading the README: "51 products across 6 categories (headphones, kitchen appliances, fitness, smart home, outdoor, apparel care)."
- Sampled manuals (`ms-head-1001`, `ms-kitc-2001`, `ms-fit-3001`, `ms-home-4001`, `ms-out-5001`, `ms-app-6001`) confirm the manuals are generic templates (box contents, setup, care, warranty, troubleshooting) with **no price and no product-specific spec numbers** — specs sections literally say "varies by model" / "see model-specific section." So prices and descriptions must be authored, not extracted.

## Desired End State

`products` contains 56 rows total (5 existing + 51 new), one per manual in the KB, with SKU derived from the manual's `document_id` (uppercased), so a citation like `[ms-head-1001]` from the `retrieve` tool corresponds directly to `sku = 'MS-HEAD-1001'` in the DB. `orders` gains a handful of sample rows referencing new SKUs so `lookup_order` has realistic multi-category data to exercise.

Verification: `sqlite3 db/marketsphere.db "SELECT COUNT(*) FROM products;"` returns 56, and every `document_id` from `manifest.jsonl` with `source_type: manuals` has a matching (uppercased) `sku` in `products`.

### Key Discoveries:
- `src/backend.py` wraps raw SQL against these two tables (referenced in CLAUDE.md); no ORM/migration framework — this is a straight SQL insert task, not a schema change.
- No status enum, no seed/fixture scripts exist in the repo — data entry is done directly against the sqlite file.

## What We're NOT Doing

- Not changing the `products`/`orders` schema.
- Not writing a reusable seed script/fixture file (out of scope — this is a one-time data backfill via direct SQL).
- Not modifying the 5 existing product rows.
- Not adding orders for every one of the 51 new products — just a representative handful across categories/statuses.
- Not attempting to extract per-model specs from the PDFs (they don't contain them) — prices/descriptions are authored estimates, consistent in style with the existing catalog.

## Implementation Approach

Single SQL script executed against `db/marketsphere.db`, in one phase: insert all 51 products, then insert sample orders referencing a subset of both old and new SKUs.

SKU convention: uppercase the manifest `document_id` (e.g. `ms-head-1001` → `MS-HEAD-1001`).
Category convention (snake_case, one per KB category): `audio`, `kitchen`, `fitness`, `smart_home`, `outdoor`, `apparel` (EverWear apparel joins the existing `apparel` category alongside `MS-SHIRT-001`).

## Phase 1: Insert 51 Products

### Overview
Insert one row per manual, derived from `manifest.jsonl` titles (stripped of the "- User Manual" suffix) with authored price/description matching the existing catalog's style and price range ($19.99–$1999.99).

### Changes Required:

#### 1. Products data
**Target**: `db/marketsphere.db`, table `products`
**Change**: Execute the following SQL (run via `sqlite3 db/marketsphere.db`):

```sql
INSERT INTO products (sku, name, category, price, description, in_stock) VALUES
-- Audio (ms-head, 7)
('MS-HEAD-1001', 'AeroPulse Wireless Over-Ear Headphones', 'audio', 79.99, 'Bluetooth 5.3, 30hr battery', 1),
('MS-HEAD-1002', 'AeroPulse Pro Studio Headphones', 'audio', 149.99, 'Studio-tuned ANC, 40mm drivers', 1),
('MS-HEAD-1003', 'AeroPulse Mini Earbuds', 'audio', 59.99, 'True wireless, IPX4', 1),
('MS-HEAD-1004', 'SilentFlow Active Noise Cancelling Headphones', 'audio', 199.99, 'Premium ANC, 22hr battery', 1),
('MS-HEAD-1005', 'BassRunner Sport Earbuds', 'audio', 49.99, 'Sweatproof, secure-fit sport earbuds', 1),
('MS-HEAD-1006', 'ClearLine Office Headset', 'audio', 39.99, 'Boom mic, noise-cancelling calls', 1),
('MS-HEAD-1007', 'AeroPulse Gaming Headphones X1', 'audio', 89.99, '7.1 surround, detachable mic', 1),

-- Kitchen (ms-kitc, 9)
('MS-KITC-2001', 'ChefPro Stand Mixer 5Qt', 'kitchen', 249.99, '5-quart bowl, 10-speed', 1),
('MS-KITC-2002', 'ChefPro Professional Blender', 'kitchen', 129.99, '1000W, variable speed', 1),
('MS-KITC-2003', 'ChefPro Air Fryer XL', 'kitchen', 119.99, '6-quart capacity, digital touch', 1),
('MS-KITC-2004', 'ChefPro Coffee Maker 12-cup', 'kitchen', 59.99, 'Programmable, 12-cup carafe', 1),
('MS-KITC-2005', 'ChefPro Espresso Machine', 'kitchen', 349.99, '15-bar pump, milk frother', 1),
('MS-KITC-2006', 'ChefPro Slow Cooker 6Qt', 'kitchen', 49.99, '6-quart, 3 heat settings', 1),
('MS-KITC-2007', 'ChefPro Toaster Oven Pro', 'kitchen', 89.99, 'Convection, 6-slice capacity', 1),
('MS-KITC-2008', 'ChefPro Immersion Blender', 'kitchen', 39.99, 'Handheld, 3-speed with whisk attachment', 1),
('MS-KITC-2009', 'ChefPro Pressure Cooker 8Qt', 'kitchen', 99.99, '8-quart, 14 preset programs', 1),

-- Fitness (ms-fit, 9)
('MS-FIT-3001', 'FlexTrack Smart Watch Series 5', 'fitness', 229.99, 'GPS, 7-day battery, 5 ATM', 1),
('MS-FIT-3002', 'FlexTrack Pro Fitness Band', 'fitness', 79.99, '14-day battery, heart rate', 1),
('MS-FIT-3003', 'FlexTrack Heart Rate Chest Strap', 'fitness', 49.99, 'Bluetooth/ANT+, IPX7', 1),
('MS-FIT-3004', 'PowerLift Adjustable Dumbbells', 'fitness', 299.99, '5-52.5 lbs per dumbbell, pair', 1),
('MS-FIT-3005', 'PowerLift Resistance Band Set', 'fitness', 29.99, '5 bands, door anchor included', 1),
('MS-FIT-3006', 'PowerLift Foam Roller Pro', 'fitness', 24.99, 'High-density, 18-inch', 1),
('MS-FIT-3007', 'FlexTrack Smart Scale', 'fitness', 39.99, 'Body composition, app sync', 1),
('MS-FIT-3008', 'PowerLift Pull-Up Bar Pro', 'fitness', 44.99, 'Doorway mount, no drilling', 1),
('MS-FIT-3009', 'PowerLift Yoga Mat Premium', 'fitness', 34.99, '6mm non-slip, carry strap', 1),

-- Smart home (ms-home, 9)
('MS-HOME-4001', 'HomeSense Smart Doorbell Pro', 'smart_home', 149.99, '1080p HD, motion alerts', 1),
('MS-HOME-4002', 'HomeSense Smart Thermostat', 'smart_home', 179.99, 'Wi-Fi, energy-saving scheduling', 1),
('MS-HOME-4003', 'HomeSense Smart Light Bulbs (4-pack)', 'smart_home', 39.99, 'Color-changing, E26 base', 1),
('MS-HOME-4004', 'HomeSense Smart Plug (2-pack)', 'smart_home', 24.99, 'Wi-Fi, voice control', 1),
('MS-HOME-4005', 'HomeSense Security Camera Indoor', 'smart_home', 59.99, '1080p, night vision', 1),
('MS-HOME-4006', 'HomeSense Security Camera Outdoor', 'smart_home', 89.99, 'Weatherproof, 2-way audio', 1),
('MS-HOME-4007', 'HomeSense Smart Lock', 'smart_home', 199.99, 'Keyless entry, auto-lock', 1),
('MS-HOME-4008', 'HomeSense Smart Hub', 'smart_home', 69.99, 'Zigbee/Z-Wave bridge', 1),
('MS-HOME-4009', 'HomeSense Motion Sensor (2-pack)', 'smart_home', 34.99, 'Battery-powered, app alerts', 1),

-- Outdoor (ms-out, 9)
('MS-OUT-5001', 'TrailMaster Hiking Backpack 40L', 'outdoor', 129.99, '40L capacity, adjustable torso fit', 1),
('MS-OUT-5002', 'TrailMaster Camping Tent 2-Person', 'outdoor', 149.99, '3-season, freestanding', 1),
('MS-OUT-5003', 'TrailMaster Camping Tent 4-Person', 'outdoor', 199.99, '3-season, freestanding', 1),
('MS-OUT-5004', 'TrailMaster Sleeping Bag 20F', 'outdoor', 89.99, '20F comfort rating, mummy shape', 1),
('MS-OUT-5005', 'TrailMaster Camp Stove', 'outdoor', 49.99, 'Compact propane, piezo ignition', 1),
('MS-OUT-5006', 'TrailMaster Headlamp Pro', 'outdoor', 29.99, '300 lumens, rechargeable', 1),
('MS-OUT-5007', 'TrailMaster Water Filter Pump', 'outdoor', 44.99, '0.2 micron, field-serviceable', 1),
('MS-OUT-5008', 'TrailMaster Inflatable Sleeping Pad', 'outdoor', 59.99, 'R-value 4.2, packable', 1),
('MS-OUT-5009', 'TrailMaster Trekking Poles Pair', 'outdoor', 39.99, 'Adjustable, cork grips', 1),

-- Apparel (ms-app, 8)
('MS-APP-6001', 'EverWear Merino Wool Base Layer', 'apparel', 64.99, 'Odor-resistant, moisture-wicking', 1),
('MS-APP-6002', 'EverWear Rain Jacket', 'apparel', 99.99, 'Waterproof shell, DWR coating', 1),
('MS-APP-6003', 'EverWear Insulated Down Jacket', 'apparel', 179.99, '650-fill down, packable', 1),
('MS-APP-6004', 'EverWear Trail Running Shoes', 'apparel', 109.99, 'Lightweight, grippy outsole', 1),
('MS-APP-6005', 'EverWear Hiking Boots', 'apparel', 139.99, 'Waterproof, ankle support', 1),
('MS-APP-6006', 'EverWear Performance Socks (3-pack)', 'apparel', 19.99, 'Moisture-wicking, cushioned', 1),
('MS-APP-6007', 'EverWear Travel Pants', 'apparel', 69.99, 'Quick-dry, stretch fabric', 1),
('MS-APP-6008', 'EverWear Long-Sleeve UV Shirt', 'apparel', 44.99, 'UPF 50+, breathable', 1);
```

### Success Criteria:

#### Automated Verification:
- [x] Row count is 56: `sqlite3 db/marketsphere.db "SELECT COUNT(*) FROM products;"`
- [x] No duplicate SKUs (PK constraint enforces this at insert time; insert fails loudly otherwise)
- [x] Every `manifest.jsonl` manual `document_id` has a matching uppercased `sku`: `sqlite3 db/marketsphere.db "SELECT sku FROM products WHERE sku LIKE 'MS-HEAD-%' OR sku LIKE 'MS-KITC-%' OR sku LIKE 'MS-FIT-%' OR sku LIKE 'MS-HOME-%' OR sku LIKE 'MS-OUT-%' OR sku LIKE 'MS-APP-%';" | wc -l` returns 51

#### Manual Verification:
- [ ] `python src/agent.py`, ask a product question that should hit `lookup_product`/`get_product_details` for a newly added SKU (e.g. "how much is the ChefPro Stand Mixer?") and confirm it returns the new row.
- [ ] Ask a question whose answer should come from the `retrieve` tool citing a manual (e.g. "how do I pair the AeroPulse headphones?") and confirm the citation SKU-like ID lines up with a real `products` row if the agent also does a lookup.

---

## Phase 2: Insert Sample Orders

### Overview
Add a handful of orders referencing new SKUs (plus keep existing 2) so `lookup_order` has multi-category data to exercise end-to-end.

### Changes Required:

#### 1. Orders data
**Target**: `db/marketsphere.db`, table `orders`
**Change**:

```sql
INSERT INTO orders (order_id, customer_email, sku, quantity, order_date, status, tracking_number) VALUES
('ORD-100003', 'customer3@example.com', 'MS-HEAD-1001', 1, '2026-06-02 10:15:00', 'delivered', 'TRACK-345678'),
('ORD-100004', 'customer4@example.com', 'MS-KITC-2001', 1, '2026-06-20 14:05:00', 'shipped', 'TRACK-456789'),
('ORD-100005', 'customer5@example.com', 'MS-FIT-3001', 2, '2026-07-01 09:40:00', 'processing', 'TRACK-567890'),
('ORD-100006', 'customer2@example.com', 'MS-HOME-4001', 1, '2026-07-15 16:20:00', 'delivered', 'TRACK-678901'),
('ORD-100007', 'customer6@example.com', 'MS-OUT-5002', 1, '2026-08-01 11:00:00', 'shipped', 'TRACK-789012'),
('ORD-100008', 'customer1@example.com', 'MS-APP-6003', 1, '2026-08-10 08:30:00', 'processing', 'TRACK-890123');
```

### Success Criteria:

#### Automated Verification:
- [x] Row count is 8: `sqlite3 db/marketsphere.db "SELECT COUNT(*) FROM orders;"`
- [x] FK integrity holds (every order `sku` exists in `products`): `sqlite3 db/marketsphere.db "SELECT o.order_id FROM orders o LEFT JOIN products p ON o.sku = p.sku WHERE p.sku IS NULL;"` returns no rows

#### Manual Verification:
- [ ] `python src/agent.py`, ask "where is my order ORD-100004?" and confirm the agent's `lookup_order` tool call returns the ChefPro Stand Mixer order with `shipped` status.

---

## Testing Strategy

### Manual Testing Steps:
1. Run both SQL blocks against `db/marketsphere.db` via `sqlite3 db/marketsphere.db < script.sql` (or interactively).
2. Run the automated verification queries above.
3. Start the agent (`python src/agent.py`) and manually test a few product-question and order-status queries spanning old and new SKUs.

## Migration Notes

This is additive-only (`INSERT`, no `UPDATE`/`DELETE`) against a local sqlite file — no rollback tooling needed beyond restoring from a `cp db/marketsphere.db db/marketsphere.db.bak` backup taken before running, in case a retry is needed.

## References

- Knowledge base manifest: `Building-RAG-from-scratch/data/knowledge_base/marketsphere_kb_meta/manifest.jsonl`
- Knowledge base README: `Building-RAG-from-scratch/data/knowledge_base/marketsphere_kb_meta/README.md`
- DB schema: `db/marketsphere.db` (`products`, `orders` tables)
