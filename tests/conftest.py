import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nutrition_coach as nc  # noqa: E402

OFF_PRODUCT = {
    "code": "3017620422003", "product_name": "Nutella", "brands": "Ferrero",
    "nutriscore_grade": "e", "serving_size": "15 g",
    "nutriments": {"energy-kcal_100g": 539, "sugars_100g": 56.3, "fat_100g": 30.9,
                   "saturated-fat_100g": 10.6, "proteins_100g": 6.3, "sodium_100g": 0.0428},
}

FDC_BODY = {"foods": [{"description": "Bananas, raw", "fdcId": 173944, "foodNutrients": [
    {"nutrientName": "Energy", "unitName": "KCAL", "value": 89},
    {"nutrientName": "Energy", "unitName": "kJ", "value": 371},
    {"nutrientName": "Protein", "unitName": "G", "value": 1.09},
    {"nutrientName": "Sugars, total including NLEA", "unitName": "G", "value": 12.2},
    {"nutrientName": "Sodium, Na", "unitName": "MG", "value": 1},
]}]}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def fake_http(monkeypatch):
    """Replaces the food databases with canned responses; returns the list of requested URLs."""
    calls = []

    def get(url, params=None, **kwargs):
        calls.append(url)
        if "nal.usda.gov" in url:
            return FakeResponse(FDC_BODY)
        if "/api/v2/product/" in url:
            return FakeResponse({"product": OFF_PRODUCT})
        return FakeResponse({"products": [OFF_PRODUCT]})

    monkeypatch.setattr(nc.requests, "get", get)
    return calls
