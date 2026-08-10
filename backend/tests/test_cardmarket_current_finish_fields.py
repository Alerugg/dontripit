from decimal import Decimal

from app.jobs.cardmarket_prices import load_price_guide_bytes


def test_current_cardmarket_foil_json_fields_are_parsed():
    payload = b'''{"priceGuides":[{
      "idProduct": 1,
      "avg": 0.09,
      "low": 0.02,
      "trend": 0.08,
      "avg1": 0.07,
      "avg7": 0.06,
      "avg30": 0.10,
      "avg-foil": 0.36,
      "low-foil": 0.04,
      "trend-foil": 0.40,
      "avg1-foil": 0.30,
      "avg7-foil": 0.33,
      "avg30-foil": 0.34
    }]}'''
    _, rows = load_price_guide_bytes(payload, filename="price_guide_1.json")
    row = rows[0]
    assert row.foil_avg == Decimal("0.36")
    assert row.foil_low == Decimal("0.04")
    assert row.foil_trend == Decimal("0.40")
    assert row.foil_avg1 == Decimal("0.30")
    assert row.foil_avg7 == Decimal("0.33")
    assert row.foil_avg30 == Decimal("0.34")


def test_pokemon_holo_json_fields_remain_supported():
    payload = b'''{"priceGuides":[{
      "idProduct": 2,
      "avg-holo": 7.0,
      "low-holo": 5.0,
      "trend-holo": 6.0,
      "avg1-holo": 5.5,
      "avg7-holo": 5.7,
      "avg30-holo": 5.9
    }]}'''
    _, rows = load_price_guide_bytes(payload, filename="price_guide_6.json")
    row = rows[0]
    assert row.foil_avg == Decimal("7.00")
    assert row.foil_low == Decimal("5.00")
    assert row.foil_trend == Decimal("6.00")
    assert row.foil_avg1 == Decimal("5.50")
    assert row.foil_avg7 == Decimal("5.70")
    assert row.foil_avg30 == Decimal("5.90")
