"""Unit tests for worst-case target-market shipping (per-country zones)."""

from syncee_scanner.extraction.records import _target_market_shipping, normalize_product

TARGETS = ["ES", "PT", "FR", "DE", "IT", "AT", "BE", "NL", "IE"]


def test_worst_case_across_zones():
    zones = [
        {"shippingCost": 2.0, "minShippingDays": 3, "maxShippingDays": 6,
         "locations": ["IE", "GB"]},                      # cheap/fast to Ireland
        {"shippingCost": 8.5, "minShippingDays": 7, "maxShippingDays": 12,
         "locations": ["ES", "PT", "FR", "DE", "IT", "AT", "BE", "NL"]},  # pricier to EU
    ]
    r = _target_market_shipping(zones, TARGETS)
    assert r["cost"] == 8.5          # most expensive target market
    assert r["max_days"] == 12       # slowest target market
    assert r["shipped"] == 9 and r["total"] == 9


def test_partial_coverage():
    zones = [{"shippingCost": 3.0, "minShippingDays": 4, "maxShippingDays": 8,
              "locations": ["IE", "FR", "DE"]}]  # only 3 of 9 targets
    r = _target_market_shipping(zones, TARGETS)
    assert r["shipped"] == 3 and r["total"] == 9


def test_no_target_coverage():
    zones = [{"shippingCost": 1.0, "minShippingDays": 2, "maxShippingDays": 5,
              "locations": ["US", "CA"]}]
    r = _target_market_shipping(zones, TARGETS)
    assert r["shipped"] == 0


def test_normalize_uses_worst_case_and_coverage():
    raw = {
        "id": "P1", "name": "x", "variants": [{}],
        "shipping_zones": [
            {"shippingCost": 2.0, "minShippingDays": 3, "maxShippingDays": 6, "locations": ["IE"]},
            {"shippingCost": 9.0, "minShippingDays": 8, "maxShippingDays": 14,
             "locations": ["ES", "DE", "FR"]},
        ],
    }
    np = normalize_product(raw, supplier_key_value="sid:1", target_codes=TARGETS)
    assert np["shipping_cost"] == 9.0
    assert np["shipping_max_days"] == 14
    assert np["target_markets_shipped"] == 4  # IE, ES, DE, FR
    assert np["target_markets_total"] == 9


def test_no_zones_falls_back():
    raw = {"id": "P1", "name": "x", "variants": [{}],
           "shipping_cost": 3.0, "shipping_max_days": 5}
    np = normalize_product(raw, supplier_key_value="sid:1", target_codes=TARGETS)
    assert np["shipping_cost"] == 3.0
    assert np["target_markets_shipped"] is None  # unknown coverage
