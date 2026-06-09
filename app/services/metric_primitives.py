"""Shared financial primitives for metrics engine and scoring models."""

from __future__ import annotations


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def total_debt(row: dict) -> float | None:
    debt = row.get("debt")
    if debt is not None:
        return debt
    debt_current = row.get("debtcurrent")
    debt_lt = row.get("debtlt")
    if debt_current is not None or debt_lt is not None:
        return (debt_current or 0.0) + (debt_lt or 0.0)
    return None


def free_cash_flow(row: dict) -> float | None:
    fcf = row.get("fcf")
    if fcf is not None:
        return fcf
    ncfo = row.get("ncfo")
    capex = row.get("capex")
    if ncfo is None:
        return None
    if capex is not None:
        return ncfo - capex
    return ncfo


def resolve_ebitda(row: dict) -> float | None:
    ebitda = row.get("ebitda")
    if ebitda is not None:
        return ebitda
    ebitda = row.get("opinc") or row.get("ebit")
    if ebitda is not None:
        return ebitda
    netinc = row.get("netinc")
    if netinc is None:
        return None
    taxexp = row.get("taxexp")
    interestexp = row.get("interestexp")
    pretax_proxy = netinc + abs(taxexp or 0)
    return pretax_proxy + abs(interestexp or 0)


def gross_profit(row: dict) -> float | None:
    gp = row.get("gp")
    if gp is not None:
        return gp
    revenue = row.get("revenue")
    cor = row.get("cor")
    if revenue is not None and cor is not None:
        return revenue - cor
    return None


def gross_margin(row: dict) -> float | None:
    return safe_div(gross_profit(row), row.get("revenue"))


def operating_margin(row: dict) -> float | None:
    revenue = row.get("revenue")
    opinc = row.get("opinc") or row.get("ebit")
    return safe_div(opinc, revenue)


def ebitda_margin(row: dict) -> float | None:
    return safe_div(resolve_ebitda(row), row.get("revenue"))


def net_margin(row: dict) -> float | None:
    return safe_div(row.get("netinc"), row.get("revenue"))


def fcf_margin(row: dict) -> float | None:
    return safe_div(free_cash_flow(row), row.get("revenue"))


def cfo_margin(row: dict) -> float | None:
    return safe_div(row.get("ncfo"), row.get("revenue"))


def current_ratio(row: dict) -> float | None:
    return safe_div(row.get("assetscurrent"), row.get("liabilitiescurrent"))


def quick_ratio(row: dict) -> float | None:
    assets_current = row.get("assetscurrent")
    inventory = row.get("inventory")
    liabilities_current = row.get("liabilitiescurrent")
    if assets_current is None:
        return None
    quick_assets = assets_current - (inventory or 0.0)
    return safe_div(quick_assets, liabilities_current)


def roa(row: dict) -> float | None:
    return safe_div(row.get("netinc"), row.get("assets"))


def roe(row: dict) -> float | None:
    return safe_div(row.get("netinc"), row.get("equity"))


def asset_turnover(row: dict) -> float | None:
    return safe_div(row.get("revenue"), row.get("assets"))


def leverage(row: dict) -> float | None:
    return safe_div(total_debt(row), row.get("assets"))


def debt_equity(row: dict) -> float | None:
    return safe_div(total_debt(row), row.get("equity"))


def interest_coverage(row: dict) -> float | None:
    ebit = row.get("ebit") or row.get("opinc")
    interest = row.get("interestexp")
    if ebit is None or interest in (None, 0):
        return None
    return ebit / abs(interest)


def cash_to_debt(row: dict) -> float | None:
    cash = row.get("cashneq")
    debt = total_debt(row)
    if cash is None:
        return None
    if debt in (None, 0):
        return None
    return cash / debt


def book_value_per_share(row: dict) -> float | None:
    shares = row.get("sharesbas")
    if shares in (None, 0):
        return None
    equity = row.get("equity")
    if equity is not None:
        return equity / shares
    assets = row.get("assets")
    liabilities = row.get("liabilities")
    if assets is not None and liabilities is not None:
        return (assets - liabilities) / shares
    return None
