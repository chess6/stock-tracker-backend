from __future__ import annotations

from app.services.sec import normalize_company_facts


def test_normalize_company_facts_maps_core_metrics():
    payload = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"val": 1000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                            {"val": 220, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-20", "end": "2025-03-31", "accn": "2"},
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {"val": 200, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {"val": 50, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"val": 10, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-20", "end": "2024-12-31", "accn": "1"},
                        ]
                    }
                }
            },
        }
    }

    rows = normalize_company_facts(7, payload)
    metrics = {(row["metric"], row["period_end"], row["dimension"]): row for row in rows}
    assert ("revenue", "2024-12-31", "ARY") in metrics
    assert ("revenue", "2025-03-31", "ARQ") in metrics
    assert ("sharesbas", "2024-12-31", "ARY") in metrics
    assert ("fcf", "2024-12-31", "ARY") in metrics
    assert metrics[("fcf", "2024-12-31", "ARY")]["value"] == 150.0
