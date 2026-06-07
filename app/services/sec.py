from __future__ import annotations

import hashlib

from ..clients.sec import SecClient

SEC_METRIC_CONFIG = {
    "revenue": {
        "taxonomy": "us-gaap",
        "concepts": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
            "RevenuesNetOfInterestExpense",
            "InterestIncomeExpenseNet",
            "TotalRevenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
        "units": ("USD",),
    },
    "cor": {
        "taxonomy": "us-gaap",
        "concepts": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
        "units": ("USD",),
    },
    "gp": {
        "taxonomy": "us-gaap",
        "concepts": ["GrossProfit"],
        "units": ("USD",),
    },
    "opex": {
        "taxonomy": "us-gaap",
        "concepts": ["OperatingExpenses", "CostsAndExpenses"],
        "units": ("USD",),
    },
    "sgna": {
        "taxonomy": "us-gaap",
        "concepts": ["SellingGeneralAndAdministrativeExpense"],
        "units": ("USD",),
    },
    "rnd": {
        "taxonomy": "us-gaap",
        "concepts": ["ResearchAndDevelopmentExpense"],
        "units": ("USD",),
    },
    "opinc": {
        "taxonomy": "us-gaap",
        "concepts": ["OperatingIncomeLoss"],
        "units": ("USD",),
    },
    "depamor": {
        "taxonomy": "us-gaap",
        "concepts": [
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "Depreciation",
        ],
        "units": ("USD",),
    },
    "netinc": {
        "taxonomy": "us-gaap",
        "concepts": ["NetIncomeLoss"],
        "units": ("USD",),
    },
    "compinc": {
        "taxonomy": "us-gaap",
        "concepts": ["ComprehensiveIncomeNetOfTax"],
        "units": ("USD",),
    },
    "taxexp": {
        "taxonomy": "us-gaap",
        "concepts": ["IncomeTaxExpenseBenefit"],
        "units": ("USD",),
    },
    "interestexp": {
        "taxonomy": "us-gaap",
        "concepts": [
            "InterestExpense",
            "InterestExpenseDebt",
            "InterestExpenseOperating",
            "InterestIncomeExpenseNet",
        ],
        "units": ("USD",),
    },
    "eps": {
        "taxonomy": "us-gaap",
        "concepts": [
            "EarningsPerShareDiluted",
            "EarningsPerShareBasicAndDiluted",
            "EarningsPerShareBasic",
        ],
        "units": ("USD/shares", "USD"),
    },
    "assets": {
        "taxonomy": "us-gaap",
        "concepts": ["Assets"],
        "units": ("USD",),
    },
    "assetscurrent": {
        "taxonomy": "us-gaap",
        "concepts": ["AssetsCurrent"],
        "units": ("USD",),
    },
    "liabilities": {
        "taxonomy": "us-gaap",
        "concepts": ["Liabilities"],
        "units": ("USD",),
    },
    "liabilitiescurrent": {
        "taxonomy": "us-gaap",
        "concepts": ["LiabilitiesCurrent"],
        "units": ("USD",),
    },
    "equity": {
        "taxonomy": "us-gaap",
        "concepts": [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ],
        "units": ("USD",),
    },
    "cashneq": {
        "taxonomy": "us-gaap",
        "concepts": [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ],
        "units": ("USD",),
    },
    "debtcurrent": {
        "taxonomy": "us-gaap",
        "concepts": ["DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"],
        "units": ("USD",),
    },
    "debtlt": {
        "taxonomy": "us-gaap",
        "concepts": [
            "LongTermDebtNoncurrent",
            "LongTermDebt",
            "LongTermDebtAndCapitalLeaseObligations",
        ],
        "units": ("USD",),
    },
    "ppnenet": {
        "taxonomy": "us-gaap",
        "concepts": ["PropertyPlantAndEquipmentNet"],
        "units": ("USD",),
    },
    "inventory": {
        "taxonomy": "us-gaap",
        "concepts": ["InventoryNet"],
        "units": ("USD",),
    },
    "receivables": {
        "taxonomy": "us-gaap",
        "concepts": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
        "units": ("USD",),
    },
    "payables": {
        "taxonomy": "us-gaap",
        "concepts": ["AccountsPayableCurrent"],
        "units": ("USD",),
    },
    "retearn": {
        "taxonomy": "us-gaap",
        "concepts": ["RetainedEarningsAccumulatedDeficit"],
        "units": ("USD",),
    },
    "goodwill": {
        "taxonomy": "us-gaap",
        "concepts": ["Goodwill"],
        "units": ("USD",),
    },
    "intangibles": {
        "taxonomy": "us-gaap",
        "concepts": ["IntangibleAssetsNetExcludingGoodwill"],
        "units": ("USD",),
    },
    "ncfo": {
        "taxonomy": "us-gaap",
        "concepts": [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ],
        "units": ("USD",),
    },
    "ncfi": {
        "taxonomy": "us-gaap",
        "concepts": [
            "NetCashProvidedByUsedInInvestingActivities",
            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
        ],
        "units": ("USD",),
    },
    "ncff": {
        "taxonomy": "us-gaap",
        "concepts": [
            "NetCashProvidedByUsedInFinancingActivities",
            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
        ],
        "units": ("USD",),
    },
    "ncfdiv": {
        "taxonomy": "us-gaap",
        "concepts": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
        "units": ("USD",),
    },
    "ncfdebt": {
        "taxonomy": "us-gaap",
        "concepts": [
            "ProceedsFromIssuanceOfLongTermDebt",
            "RepaymentsOfLongTermDebt",
            "ProceedsFromRepaymentsOfShortTermDebt",
        ],
        "units": ("USD",),
    },
    "ncfcommon": {
        "taxonomy": "us-gaap",
        "concepts": ["PaymentsForRepurchaseOfCommonStock"],
        "units": ("USD",),
    },
    "capex": {
        "taxonomy": "us-gaap",
        "concepts": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "units": ("USD",),
    },
    "sbcomp": {
        "taxonomy": "us-gaap",
        "concepts": ["ShareBasedCompensation", "StockBasedCompensation", "AllocatedShareBasedCompensationExpense"],
        "units": ("USD",),
    },
    "sharesbas": {
        "sources": [
            {
                "taxonomy": "dei",
                "concepts": [
                    "EntityCommonStockSharesOutstanding",
                ],
            },
            {
                "taxonomy": "us-gaap",
                "concepts": [
                    "CommonStockSharesOutstanding",
                    "CommonStockSharesIssued",
                    "WeightedAverageNumberOfSharesOutstandingBasic",
                ],
            },
        ],
        "units": ("shares",),
    },
}


def infer_dimension(form: str | None, fp: str | None) -> tuple[str | None, str | None]:
    normalized_form = (form or "").upper()
    normalized_fp = (fp or "").upper()
    if normalized_form in {"10-K", "20-F", "40-F"}:
        return "annual", "ARY"
    if normalized_form in {"10-Q", "10-Q/A", "6-K"}:
        return "quarterly", "ARQ"
    if normalized_fp == "FY":
        return "annual", "ARY"
    if normalized_fp.startswith("Q"):
        return "quarterly", "ARQ"
    return None, None


def _observation_record(company_id: int, metric: str, config: dict, concept: str, unit_name: str, observation: dict) -> dict | None:
    period_type, dimension = infer_dimension(observation.get("form"), observation.get("fp"))
    if not period_type or not observation.get("end"):
        return None
    value = observation.get("val")
    if value is None:
        return None
    return {
        "company_id": company_id,
        "metric": metric,
        "value": value,
        "unit": unit_name,
        "period_end": observation.get("end"),
        "period_type": period_type,
        "dimension": dimension,
        "fiscal_year": observation.get("fy"),
        "fiscal_quarter": observation.get("fp"),
        "filing_date": observation.get("filed"),
        "form": observation.get("form"),
        "accession": observation.get("accn"),
        "source": "sec_companyfacts",
        "taxonomy": config["taxonomy"],
        "xbrl_concept": concept,
    }


def _metric_sources(config: dict) -> list[dict]:
    if "sources" in config:
        return config["sources"]
    return [{"taxonomy": config["taxonomy"], "concepts": config["concepts"]}]


def _sharesbas_priority(concept: str) -> int:
    order = {
        "EntityCommonStockSharesOutstanding": 0,
        "CommonStockSharesOutstanding": 1,
        "CommonStockSharesIssued": 2,
        "WeightedAverageNumberOfSharesOutstandingBasic": 3,
        "EntityPublicFloat": 4,
    }
    return order.get(concept, 99)


def _extract_configured_metrics(company_id: int, facts: dict) -> list[dict]:
    results_by_key: dict[tuple, dict] = {}
    for metric, config in SEC_METRIC_CONFIG.items():
        for source in _metric_sources(config):
            taxonomy_facts = facts.get(source["taxonomy"], {})
            for concept in source["concepts"]:
                concept_payload = taxonomy_facts.get(concept)
                if not concept_payload:
                    continue
                units = concept_payload.get("units", {})
                for unit_name, observations in units.items():
                    if config["units"] and unit_name not in config["units"]:
                        continue
                    for observation in observations:
                        record = _observation_record(
                            company_id,
                            metric,
                            {**config, "taxonomy": source["taxonomy"]},
                            concept,
                            unit_name,
                            observation,
                        )
                        if not record:
                            continue
                        if metric == "sharesbas" and not record["value"]:
                            continue
                        if metric == "sharesbas":
                            key = (metric, record["period_end"], record["dimension"])
                            existing = results_by_key.get(key)
                            if existing is None:
                                results_by_key[key] = record
                                continue
                            if _sharesbas_priority(concept) < _sharesbas_priority(existing["xbrl_concept"]):
                                results_by_key[key] = record
                            elif (
                                _sharesbas_priority(concept) == _sharesbas_priority(existing["xbrl_concept"])
                                and (record.get("filing_date") or "") > (existing.get("filing_date") or "")
                            ):
                                results_by_key[key] = record
                            continue
                        key = (
                            metric,
                            record["period_end"],
                            record["dimension"],
                            record["filing_date"],
                            record["accession"],
                        )
                        results_by_key.setdefault(key, record)
    return list(results_by_key.values())


def _period_index(records: list[dict]) -> dict[tuple[int, str, str], dict[str, float]]:
    index: dict[tuple[int, str, str], dict[str, float]] = {}
    for record in records:
        key = (record["company_id"], record["dimension"], record["period_end"])
        index.setdefault(key, {})[record["metric"]] = float(record["value"])
    return index


def _append_derived(records: list[dict], index: dict[tuple[int, str, str], dict[str, float]], template: dict, metric: str, value: float, concept: str) -> None:
    derived_key = (template["company_id"], template["dimension"], template["period_end"], metric)
    if any(
        record["metric"] == metric
        and record["company_id"] == template["company_id"]
        and record["dimension"] == template["dimension"]
        and record["period_end"] == template["period_end"]
        for record in records
    ):
        return
    records.append(
        {
            **template,
            "metric": metric,
            "value": value,
            "xbrl_concept": concept,
            "source": "sec_companyfacts_derived",
        }
    )
    index.setdefault((template["company_id"], template["dimension"], template["period_end"]), {})[metric] = value


def _apply_derived_metrics(records: list[dict]) -> None:
    index = _period_index(records)
    templates = {
        (record["company_id"], record["dimension"], record["period_end"]): record
        for record in records
    }

    for key, metrics in index.items():
        template = templates.get(key)
        if not template:
            continue

        revenue = metrics.get("revenue")
        cor = metrics.get("cor")
        if metrics.get("gp") is None and revenue is not None and cor is not None:
            _append_derived(records, index, template, "gp", revenue - cor, "derived_gp")

        if metrics.get("ebit") is None and metrics.get("opinc") is not None:
            _append_derived(records, index, template, "ebit", metrics["opinc"], "derived_ebit")

        opinc = metrics.get("opinc")
        dep = metrics.get("depamor")
        if metrics.get("ebitda") is None and opinc is not None and dep is not None:
            _append_derived(records, index, template, "ebitda", opinc + abs(dep), "derived_ebitda")

        assets = metrics.get("assets")
        liabilities = metrics.get("liabilities")
        if metrics.get("equity") is None and assets is not None and liabilities is not None:
            _append_derived(records, index, template, "equity", assets - liabilities, "derived_equity")

        assets_current = metrics.get("assetscurrent")
        liabilities_current = metrics.get("liabilitiescurrent")
        if metrics.get("workingcapital") is None and assets_current is not None and liabilities_current is not None:
            _append_derived(
                records,
                index,
                template,
                "workingcapital",
                assets_current - liabilities_current,
                "derived_workingcapital",
            )

        debt_current = metrics.get("debtcurrent")
        debt_lt = metrics.get("debtlt")
        if metrics.get("debt") is None and (debt_current is not None or debt_lt is not None):
            _append_derived(
                records,
                index,
                template,
                "debt",
                (debt_current or 0.0) + (debt_lt or 0.0),
                "derived_debt",
            )

        ncfo = metrics.get("ncfo")
        capex = metrics.get("capex")
        if metrics.get("fcf") is None and ncfo is not None and capex is not None:
            _append_derived(records, index, template, "fcf", ncfo - capex, "derived_fcf")

        ncfi = metrics.get("ncfi")
        ncff = metrics.get("ncff")
        if metrics.get("ncf") is None and ncfo is not None and ncfi is not None and ncff is not None:
            _append_derived(records, index, template, "ncf", ncfo + ncfi + ncff, "derived_ncf")


def normalize_company_facts(company_id: int, payload: dict) -> list[dict]:
    facts = payload.get("facts", {})
    records = _extract_configured_metrics(company_id, facts)
    _apply_derived_metrics(records)
    return records


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["SecClient", "SEC_METRIC_CONFIG", "normalize_company_facts", "infer_dimension", "content_hash"]
