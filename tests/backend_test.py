import sys


from src.backend import MarketSphereBackend

backend = MarketSphereBackend('db/marketsphere.db')

# Test get_product
product = backend.get_product('MS-LAPTOP-001')
assert product is not None, "Product should exist"
assert product['sku'] == 'MS-LAPTOP-001', "SKU should match"
assert product['name'] == 'Dell XPS 13', "Name should match"
print("✓ get_product test passed")

# Test get_order
order = backend.get_order('ORD-100001')
assert order is not None, "Order should exist"
assert order['order_id'] == 'ORD-100001', "Order ID should match"
assert order['status'] == 'delivered', "Status should be delivered"
print("✓ get_order test passed")

# Test search_products
results = backend.search_products('LAPTOP')
assert len(results) > 0, "Should find laptop products"
assert any(r['sku'] == 'MS-LAPTOP-001' for r in results), "Should find Dell XPS"
print("✓ search_products test passed")

# Test non-existent product
missing = backend.get_product('MS-NONEXISTENT')
assert missing is None, "Non-existent product should return None"
print("✓ Non-existent product test passed")

backend.close()
print("\n✅ All tests passed!")