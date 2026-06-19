#!/usr/bin/env python3
"""Point-in-time composite rank backtest using historical rank snapshots and prices."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app import create_app
from app.db import get_db
from app.repositories import Repository
from app.services.composite_ranking import _COMPOSITE_PRESETS


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, _days_in_month(year, month))
    return date(year, month, day)


def _rebalance_dates(start: date, end: date, freq: str) -> list[date]:
    step_months = 3 if freq == "quarterly" else 1
    dates: list[date] = []
    current = start
    while current <= end:
        dates.append(current)
        current = _add_months(current, step_months)
    return dates


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    avg = _mean(values)
    if avg is None:
        return None
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


@dataclass
class BacktestConfig:
    composite: str = "deep_value"
    start_date: str = ""
    end_date: str = ""
    top_k: int = 20
    score_threshold: float | None = None
    rebalance_freq: str = "monthly"
    filing_lag_days: int = 45


@dataclass
class BacktestTrade:
    entry_date: str
    exit_date: str
    ticker: str
    entry_price: float
    exit_price: float
    return_pct: float


@dataclass
class BacktestResult:
    config: BacktestConfig
    periods: list[dict[str, Any]] = field(default_factory=list)
    trades: list[BacktestTrade] = field(default_factory=list)
    metrics: dict[str, float | int | None] = field(default_factory=dict)
    error: str | None = None


class BacktestEngine:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def run(self, config: BacktestConfig) -> BacktestResult:
        composite_key = (config.composite or "deep_value").strip().lower()
        if composite_key not in _COMPOSITE_PRESETS:
            return BacktestResult(config=config, error=f"Unknown composite: {composite_key}")

        try:
            start = _parse_date(config.start_date)
            end = _parse_date(config.end_date)
        except ValueError:
            return BacktestResult(config=config, error="Invalid start_date or end_date")

        if end <= start:
            return BacktestResult(config=config, error="end_date must be after start_date")

        rebalance_dates = _rebalance_dates(start, end, config.rebalance_freq)
        if len(rebalance_dates) < 2:
            return BacktestResult(config=config, error="insufficient_rebalance_periods")

        periods: list[dict[str, Any]] = []
        trades: list[BacktestTrade] = []
        period_returns: list[float] = []
        equity = 1.0
        equity_curve: list[float] = [equity]
        prior_holdings: set[str] = set()

        for index in range(len(rebalance_dates) - 1):
            entry_date = rebalance_dates[index]
            exit_date = rebalance_dates[index + 1]
            signal = self._get_signal_on_date(config, entry_date)
            if not signal:
                periods.append(
                    {
                        "entryDate": entry_date.isoformat(),
                        "exitDate": exit_date.isoformat(),
                        "signalDate": None,
                        "holdings": [],
                        "portfolioReturn": None,
                        "skipped": True,
                    }
                )
                equity_curve.append(equity)
                continue

            signal_date, holdings = signal
            returns = self._get_period_returns(
                [ticker for ticker, _ in holdings],
                entry_date.isoformat(),
                exit_date.isoformat(),
            )
            if not returns:
                periods.append(
                    {
                        "entryDate": entry_date.isoformat(),
                        "exitDate": exit_date.isoformat(),
                        "signalDate": signal_date,
                        "holdings": [ticker for ticker, _ in holdings],
                        "portfolioReturn": None,
                        "skipped": True,
                    }
                )
                equity_curve.append(equity)
                continue

            portfolio_return = _mean(list(returns.values()))
            if portfolio_return is None:
                equity_curve.append(equity)
                continue

            period_returns.append(portfolio_return)
            equity *= 1.0 + portfolio_return
            equity_curve.append(equity)

            current_holdings = set(returns)
            turnover = None
            if prior_holdings:
                changed = len(current_holdings.symmetric_difference(prior_holdings))
                turnover = changed / max(len(current_holdings | prior_holdings), 1)
            prior_holdings = current_holdings

            periods.append(
                {
                    "entryDate": entry_date.isoformat(),
                    "exitDate": exit_date.isoformat(),
                    "signalDate": signal_date,
                    "holdings": sorted(current_holdings),
                    "portfolioReturn": round(portfolio_return, 6),
                    "turnover": round(turnover, 4) if turnover is not None else None,
                    "skipped": False,
                }
            )

            for ticker, return_pct in returns.items():
                entry_price = self.repo.fetch_price_near_date(ticker, entry_date.isoformat())
                exit_price = self.repo.fetch_price_near_date(ticker, exit_date.isoformat())
                if entry_price is None or exit_price is None:
                    continue
                trades.append(
                    BacktestTrade(
                        entry_date=entry_date.isoformat(),
                        exit_date=exit_date.isoformat(),
                        ticker=ticker,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        return_pct=round(return_pct, 6),
                    )
                )

        if not period_returns:
            return BacktestResult(
                config=config,
                periods=periods,
                trades=trades,
                error="no_valid_period_returns",
            )

        metrics = self._compute_metrics(
            period_returns=period_returns,
            equity_curve=equity_curve,
            periods=periods,
            start=start,
            end=end,
            rebalance_freq=config.rebalance_freq,
        )
        return BacktestResult(config=config, periods=periods, trades=trades, metrics=metrics)

    def _get_signal_on_date(
        self,
        config: BacktestConfig,
        rebalance_date: date,
    ) -> tuple[str, list[tuple[str, float]]] | None:
        composite_key = config.composite.strip().lower()
        signal_cutoff = rebalance_date - timedelta(days=config.filing_lag_days)
        row = self.repo.conn.execute(
            """
            SELECT MAX(snapshot_date) AS snapshot_date
            FROM company_rank_snapshots
            WHERE composite = ? AND snapshot_date <= ?
            """,
            (composite_key, signal_cutoff.isoformat()[:10]),
        ).fetchone()
        snap_date = row["snapshot_date"] if row else None
        if not snap_date:
            return None

        rows = self.repo.fetch_rank_snapshot_rows(
            composite=composite_key,
            snapshot_date=str(snap_date),
        )
        filtered: list[tuple[str, float]] = []
        for row in rows:
            score = row.get("composite_score")
            if score is None:
                continue
            if config.score_threshold is not None and score < config.score_threshold:
                continue
            filtered.append((row["ticker"], float(score)))

        if not filtered:
            return None

        top = filtered[: max(1, int(config.top_k))]
        return snap_date, top

    def _get_period_returns(
        self,
        tickers: list[str],
        entry_date: str,
        exit_date: str,
    ) -> dict[str, float]:
        returns: dict[str, float] = {}
        for ticker in tickers:
            entry_price = self.repo.fetch_price_near_date(ticker, entry_date)
            exit_price = self.repo.fetch_price_near_date(ticker, exit_date)
            if entry_price is None or exit_price is None or entry_price <= 0:
                continue
            returns[ticker] = (exit_price - entry_price) / entry_price
        return returns

    def _compute_metrics(
        self,
        *,
        period_returns: list[float],
        equity_curve: list[float],
        periods: list[dict[str, Any]],
        start: date,
        end: date,
        rebalance_freq: str,
    ) -> dict[str, float | int | None]:
        years = max((end - start).days / 365.25, 1 / 365.25)
        final_equity = equity_curve[-1]
        cagr = (final_equity ** (1.0 / years)) - 1.0 if final_equity > 0 else None

        std = _stddev(period_returns)
        avg_return = _mean(period_returns)
        periods_per_year = 4 if rebalance_freq == "quarterly" else 12
        sharpe = None
        if std and std > 0 and avg_return is not None:
            sharpe = (avg_return / std) * math.sqrt(periods_per_year)

        peak = equity_curve[0]
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                drawdown = (value - peak) / peak
                max_drawdown = min(max_drawdown, drawdown)

        calmar = None
        if cagr is not None and max_drawdown < 0:
            calmar = cagr / abs(max_drawdown)

        turnovers = [item["turnover"] for item in periods if item.get("turnover") is not None]
        turnover = _mean(turnovers) if turnovers else None

        win_rate = sum(1 for value in period_returns if value > 0) / len(period_returns)

        return {
            "cagr": round(cagr, 6) if cagr is not None else None,
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "maxDrawdown": round(max_drawdown, 6),
            "calmar": round(calmar, 4) if calmar is not None else None,
            "turnover": round(turnover, 4) if turnover is not None else None,
            "winRate": round(win_rate, 4),
            "periodsEvaluated": len(period_returns),
            "finalEquity": round(final_equity, 6),
        }


def _result_to_json(result: BacktestResult) -> dict[str, Any]:
    return {
        "config": asdict(result.config),
        "metrics": result.metrics,
        "periods": result.periods,
        "trades": [asdict(trade) for trade in result.trades],
        "error": result.error,
    }


def _write_csv(path: Path, trades: list[BacktestTrade]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["date", "ticker", "entry_price", "exit_price", "return_pct"],
        )
        writer.writeheader()
        for trade in trades:
            writer.writerow(
                {
                    "date": trade.entry_date,
                    "ticker": trade.ticker,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "return_pct": trade.return_pct,
                }
            )


def run_backtest(config: BacktestConfig, *, database_path: str | None = None) -> BacktestResult:
    app_config = None
    if database_path:
        from app.config import Config

        app_config = Config(database_path=database_path)

    app = create_app(app_config)
    with app.app_context():
        repo = Repository(get_db())
        return BacktestEngine(repo).run(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", dest="database", help="SQLite database path")
    parser.add_argument("--composite", default="deep_value", help="Composite preset key")
    parser.add_argument("--start", required=True, dest="start_date", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, dest="end_date", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--top-k", type=int, default=20, help="Number of top-ranked tickers per period")
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=None,
        help="Minimum composite score to include a ticker",
    )
    parser.add_argument(
        "--rebalance-freq",
        choices=("monthly", "quarterly"),
        default="monthly",
        help="Rebalance frequency",
    )
    parser.add_argument(
        "--filing-lag-days",
        type=int,
        default=45,
        help="Days between public filing availability and signal use",
    )
    parser.add_argument("--output", help="Write JSON results to this path")
    parser.add_argument("--csv", help="Write trade CSV to this path")
    args = parser.parse_args(argv)

    config = BacktestConfig(
        composite=args.composite,
        start_date=args.start_date,
        end_date=args.end_date,
        top_k=args.top_k,
        score_threshold=args.score_threshold,
        rebalance_freq=args.rebalance_freq,
        filing_lag_days=args.filing_lag_days,
    )
    result = run_backtest(config, database_path=args.database)
    payload = _result_to_json(result)

    if args.output:
        Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, indent=2))

    if args.csv and result.trades:
        _write_csv(Path(args.csv), result.trades)

    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
