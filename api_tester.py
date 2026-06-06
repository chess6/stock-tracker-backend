#!/usr/bin/env python3
"""CLI for exercising individual Stock Tracker API endpoints."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE = "http://localhost:5000/api"


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, object]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=600) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload) if payload else {}
    except HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            parsed = json.loads(payload) if payload else {"error": exc.reason}
        except json.JSONDecodeError:
            parsed = {"error": payload or exc.reason}
        return exc.code, parsed
    except URLError as exc:
        return 0, {"error": str(exc.reason)}


def _print_result(name: str, status: int, payload: object) -> int:
    print(f"\n=== {name} (HTTP {status}) ===")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if 200 <= status < 300 else 1


def _tickers_arg(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def cmd_search(base: str, args: argparse.Namespace) -> int:
    query = urlencode({"q": args.query})
    status, payload = _request("GET", f"{base}/search?{query}")
    return _print_result("search", status, payload)


def cmd_financials(base: str, args: argparse.Namespace) -> int:
    tickers = ",".join(_tickers_arg(args.tickers))
    params = {"ticker": tickers}
    if args.dimension:
        params["dimension"] = args.dimension
    if args.gte:
        params["gte"] = args.gte
    if args.most_recent:
        params["mostRecent"] = "true"
    status, payload = _request("GET", f"{base}/ticker/financials?{urlencode(params)}")
    if status == 200 and args.summary and isinstance(payload, dict):
        metrics = payload.get("metrics", {})
        print("\n--- metrics summary ---")
        for ticker, values in metrics.items():
            populated = {k: v for k, v in (values or {}).items() if v is not None}
            missing = [k for k, v in (values or {}).items() if v is None]
            print(f"{ticker}: populated={list(populated.keys())} missing={missing}")
    return _print_result("financials", status, payload if not args.summary else {"metrics": payload.get("metrics", {})})


def cmd_top(base: str, args: argparse.Namespace) -> int:
    tickers = ",".join(_tickers_arg(args.tickers))
    status, payload = _request("GET", f"{base}/tickers/top?{urlencode({'tickers': tickers})}")
    return _print_result("top-of-book", status, payload)


def cmd_daily_change(base: str, args: argparse.Namespace) -> int:
    tickers = ",".join(_tickers_arg(args.tickers))
    status, payload = _request("GET", f"{base}/tickers/daily-change?{urlencode({'tickers': tickers})}")
    return _print_result("daily-change", status, payload)


def cmd_insiders(base: str, args: argparse.Namespace) -> int:
    tickers = ",".join(_tickers_arg(args.tickers))
    status, payload = _request("GET", f"{base}/insiders/buying-sums?{urlencode({'tickers': tickers})}")
    return _print_result("insider-buying-sums", status, payload)


def cmd_status(base: str, _args: argparse.Namespace) -> int:
    status, payload = _request("GET", f"{base}/admin/status")
    return _print_result("admin-status", status, payload)


def cmd_bootstrap(base: str, args: argparse.Namespace) -> int:
    tickers = _tickers_arg(args.tickers)
    status, payload = _request("POST", f"{base}/admin/bootstrap?{urlencode({'tickers': ','.join(tickers)})}")
    return _print_result("bootstrap", status, payload)


def cmd_refresh_prices(base: str, args: argparse.Namespace) -> int:
    tickers = _tickers_arg(args.tickers)
    status, payload = _request("POST", f"{base}/admin/refresh-prices?{urlencode({'tickers': ','.join(tickers)})}")
    return _print_result("refresh-prices", status, payload)


def cmd_refresh_fundamentals(base: str, args: argparse.Namespace) -> int:
    tickers = _tickers_arg(args.tickers)
    status, payload = _request(
        "POST",
        f"{base}/admin/refresh-fundamentals?{urlencode({'tickers': ','.join(tickers)})}",
    )
    return _print_result("refresh-fundamentals", status, payload)


def cmd_portfolio(base: str, args: argparse.Namespace) -> int:
    tickers = _tickers_arg(args.tickers)
    exit_code = 0
    for name, fn in (
        ("financials", lambda: cmd_financials(base, argparse.Namespace(
            tickers=",".join(tickers), dimension=None, gte=None, most_recent=True, summary=True,
        ))),
        ("top-of-book", lambda: cmd_top(base, argparse.Namespace(tickers=",".join(tickers)))),
        ("daily-change", lambda: cmd_daily_change(base, argparse.Namespace(tickers=",".join(tickers)))),
        ("insider-buying-sums", lambda: cmd_insiders(base, argparse.Namespace(tickers=",".join(tickers)))),
    ):
        result = fn()
        exit_code = exit_code or result
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test Stock Tracker API endpoints individually.")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"API base URL (default: {DEFAULT_BASE})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="GET /api/search")
    search.add_argument("query")
    search.set_defaults(handler=cmd_search)

    financials = subparsers.add_parser("financials", help="GET /api/ticker/financials")
    financials.add_argument("--tickers", required=True)
    financials.add_argument("--dimension", default=None)
    financials.add_argument("--gte")
    financials.add_argument("--most-recent", action="store_true", default=True)
    financials.add_argument("--summary", action="store_true", help="Print compact metrics summary")
    financials.set_defaults(handler=cmd_financials)

    top = subparsers.add_parser("top", help="GET /api/tickers/top")
    top.add_argument("--tickers", required=True)
    top.set_defaults(handler=cmd_top)

    daily = subparsers.add_parser("daily-change", help="GET /api/tickers/daily-change")
    daily.add_argument("--tickers", required=True)
    daily.set_defaults(handler=cmd_daily_change)

    insiders = subparsers.add_parser("insiders", help="GET /api/insiders/buying-sums")
    insiders.add_argument("--tickers", required=True)
    insiders.set_defaults(handler=cmd_insiders)

    status = subparsers.add_parser("status", help="GET /api/admin/status")
    status.set_defaults(handler=cmd_status)

    bootstrap = subparsers.add_parser("bootstrap", help="POST /api/admin/bootstrap")
    bootstrap.add_argument("--tickers", required=True)
    bootstrap.set_defaults(handler=cmd_bootstrap)

    prices = subparsers.add_parser("refresh-prices", help="POST /api/admin/refresh-prices")
    prices.add_argument("--tickers", required=True)
    prices.set_defaults(handler=cmd_refresh_prices)

    fundamentals = subparsers.add_parser("refresh-fundamentals", help="POST /api/admin/refresh-fundamentals")
    fundamentals.add_argument("--tickers", required=True)
    fundamentals.set_defaults(handler=cmd_refresh_fundamentals)

    portfolio = subparsers.add_parser("portfolio", help="Run portfolio page API bundle")
    portfolio.add_argument("--tickers", required=True)
    portfolio.set_defaults(handler=cmd_portfolio)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args.base, args)


if __name__ == "__main__":
    sys.exit(main())
