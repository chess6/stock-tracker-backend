"""Backtest MVP — BacktestEngine point-in-time signal and metrics."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import get_db
from app.repositories import Repository
from scripts.backtest import (
    BacktestConfig,
    BacktestEngine,
    _add_months,
    _mean,
    _rebalance_dates,
    _stddev,
    run_backtest,
)


def _compute_metrics_standalone(*args, **kwargs):
    engine = BacktestEngine.__new__(BacktestEngine)
    return BacktestEngine._compute_metrics(engine, *args, **kwargs)


def _seed_backtest_fixture(repo: Repository) -> None:
    repo.upsert_companies(
        [
            {"ticker": "WINR", "name": "Winner Inc", "cik": "0000000101"},
            {"ticker": "LOSR", "name": "Loser Inc", "cik": "0000000102"},
            {"ticker": "MIDZ", "name": "Middle Inc", "cik": "0000000103"},
        ]
    )

    prices = {
        "WINR": [
            ("2023-10-01", 100.0),
            ("2023-11-01", 110.0),
            ("2023-12-01", 121.0),
        ],
        "LOSR": [
            ("2023-10-01", 100.0),
            ("2023-11-01", 90.0),
            ("2023-12-01", 81.0),
        ],
        "MIDZ": [
            ("2023-10-01", 100.0),
            ("2023-11-01", 100.0),
            ("2023-12-01", 100.0),
        ],
    }
    for ticker, rows in prices.items():
        repo.upsert_prices(
            ticker,
            [
                {
                    "date": day,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000,
                }
                for day, close in rows
            ],
            source="test",
        )

    repo.upsert_company_rank_snapshots(
        [
            {
                "ticker": "WINR",
                "composite": "deep_value",
                "snapshot_date": "2023-08-15",
                "composite_score": 0.95,
                "rank_in_universe": 1,
                "factors": [],
            },
            {
                "ticker": "MIDZ",
                "composite": "deep_value",
                "snapshot_date": "2023-08-15",
                "composite_score": 0.60,
                "rank_in_universe": 2,
                "factors": [],
            },
            {
                "ticker": "LOSR",
                "composite": "deep_value",
                "snapshot_date": "2023-08-15",
                "composite_score": 0.20,
                "rank_in_universe": 3,
                "factors": [],
            },
        ]
    )


def test_metric_helpers():
    assert _mean([0.05, 0.05]) == pytest.approx(0.05)
    assert _stddev([0.01, 0.02, -0.005, 0.015]) is not None

    metrics = _compute_metrics_standalone(
        period_returns=[0.05, 0.05],
        equity_curve=[1.0, 1.05, 1.1025],
        periods=[{"turnover": 0.0}, {"turnover": 0.0}],
        start=date(2023, 10, 1),
        end=date(2023, 12, 1),
        rebalance_freq="monthly",
    )
    assert metrics["winRate"] == 1.0
    assert metrics["finalEquity"] == pytest.approx(1.1025)
    assert metrics["maxDrawdown"] <= 0


def test_rebalance_date_generators():
    monthly = _rebalance_dates(date(2023, 10, 1), date(2023, 12, 1), "monthly")
    assert monthly == [date(2023, 10, 1), date(2023, 11, 1), date(2023, 12, 1)]
    assert _add_months(date(2023, 10, 1), 3) == date(2024, 1, 1)


def test_get_signal_on_date_respects_filing_lag_and_top_k(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_backtest_fixture(repo)
        engine = BacktestEngine(repo)
        config = BacktestConfig(
            composite="deep_value",
            start_date="2023-11-01",
            end_date="2023-12-01",
            top_k=1,
            filing_lag_days=45,
        )
        signal = engine._get_signal_on_date(config, date(2023, 11, 1))
        assert signal is not None
        snap_date, holdings = signal
        assert snap_date == "2023-08-15"
        assert holdings == [("WINR", 0.95)]


def test_get_period_returns(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_backtest_fixture(repo)
        engine = BacktestEngine(repo)
        returns = engine._get_period_returns(["WINR", "LOSR"], "2023-10-01", "2023-11-01")
        assert returns["WINR"] == pytest.approx(0.10)
        assert returns["LOSR"] == pytest.approx(-0.10)


def test_run_backtest_monthly_top_two(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_backtest_fixture(repo)
        result = BacktestEngine(repo).run(
            BacktestConfig(
                composite="deep_value",
                start_date="2023-10-01",
                end_date="2023-12-01",
                top_k=2,
                rebalance_freq="monthly",
                filing_lag_days=45,
            )
        )

        assert result.error is None
        assert result.metrics["periodsEvaluated"] == 2
        assert result.metrics["winRate"] == 1.0
        assert result.metrics["finalEquity"] == pytest.approx(1.1025, rel=0.01)
        assert result.metrics["maxDrawdown"] is not None
        assert result.metrics["cagr"] is not None
        assert len(result.trades) == 4


def test_run_backtest_rejects_unknown_composite(app):
    with app.app_context():
        repo = Repository(get_db())
        result = BacktestEngine(repo).run(
            BacktestConfig(
                composite="not_a_preset",
                start_date="2023-10-01",
                end_date="2023-12-01",
            )
        )
        assert result.error == "Unknown composite: not_a_preset"


def test_run_backtest_score_threshold_filters_holdings(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_backtest_fixture(repo)
        result = BacktestEngine(repo).run(
            BacktestConfig(
                composite="deep_value",
                start_date="2023-10-01",
                end_date="2023-12-01",
                top_k=10,
                score_threshold=0.70,
                rebalance_freq="monthly",
                filing_lag_days=45,
            )
        )
        assert result.error is None
        nov_period = result.periods[0]
        assert nov_period["holdings"] == ["WINR"]


def test_backtest_cli_json_output(app, tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with app.app_context():
        repo = Repository(get_db())
        _seed_backtest_fixture(repo)

    from scripts.backtest import main

    out_file = tmp_path / "bt.json"
    exit_code = main(
        [
            "--db",
            str(db_path),
            "--start",
            "2023-10-01",
            "--end",
            "2023-12-01",
            "--top-k",
            "2",
            "--output",
            str(out_file),
        ]
    )
    assert exit_code == 0
    assert out_file.exists()
    payload = out_file.read_text(encoding="utf-8")
    assert '"cagr"' in payload
    assert '"periodsEvaluated"' in payload


def test_run_backtest_helper_uses_database_path(app, tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with app.app_context():
        _seed_backtest_fixture(Repository(get_db()))

    result = run_backtest(
        BacktestConfig(
            composite="deep_value",
            start_date="2023-10-01",
            end_date="2023-12-01",
            top_k=2,
            filing_lag_days=45,
        ),
        database_path=str(db_path),
    )
    assert result.error is None
    assert result.metrics["periodsEvaluated"] == 2


def _seed_lookahead_fixture(repo: Repository) -> None:
    """Older snapshot ranks WINR first; newer post-cutoff snapshot would rank LOSR first."""
    repo.upsert_companies(
        [
            {"ticker": "WINR", "name": "Winner Inc", "cik": "0000000101"},
            {"ticker": "LOSR", "name": "Loser Inc", "cik": "0000000102"},
        ]
    )
    for ticker, close in (("WINR", 100.0), ("LOSR", 100.0)):
        repo.upsert_prices(
            ticker,
            [
                {"date": "2023-10-01", "open": close, "high": close, "low": close, "close": close, "volume": 1},
                {"date": "2023-11-01", "open": close, "high": close, "low": close, "close": close, "volume": 1},
            ],
            source="test",
        )
    repo.upsert_company_rank_snapshots(
        [
            {
                "ticker": "WINR",
                "composite": "deep_value",
                "snapshot_date": "2023-08-15",
                "composite_score": 0.95,
                "rank_in_universe": 1,
                "factors": [],
            },
            {
                "ticker": "LOSR",
                "composite": "deep_value",
                "snapshot_date": "2023-08-15",
                "composite_score": 0.10,
                "rank_in_universe": 2,
                "factors": [],
            },
            {
                "ticker": "LOSR",
                "composite": "deep_value",
                "snapshot_date": "2023-09-20",
                "composite_score": 0.99,
                "rank_in_universe": 1,
                "factors": [],
            },
            {
                "ticker": "WINR",
                "composite": "deep_value",
                "snapshot_date": "2023-09-20",
                "composite_score": 0.05,
                "rank_in_universe": 2,
                "factors": [],
            },
        ]
    )


def test_no_lookahead_ignores_snapshots_after_filing_lag_cutoff(app):
    """Oct-01 rebalance with 45d lag -> cutoff Aug-17; Sep-20 snapshot must not be used."""
    with app.app_context():
        repo = Repository(get_db())
        _seed_lookahead_fixture(repo)
        engine = BacktestEngine(repo)
        config = BacktestConfig(composite="deep_value", filing_lag_days=45, top_k=1)
        signal = engine._get_signal_on_date(config, date(2023, 10, 1))
        assert signal is not None
        snap_date, holdings = signal
        assert snap_date == "2023-08-15"
        assert holdings[0][0] == "WINR"


def test_signal_uses_latest_eligible_snapshot_not_oldest(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_lookahead_fixture(repo)
        repo.upsert_company_rank_snapshots(
            [
                {
                    "ticker": "WINR",
                    "composite": "deep_value",
                    "snapshot_date": "2023-08-01",
                    "composite_score": 0.50,
                    "rank_in_universe": 2,
                    "factors": [],
                },
                {
                    "ticker": "LOSR",
                    "composite": "deep_value",
                    "snapshot_date": "2023-08-01",
                    "composite_score": 0.90,
                    "rank_in_universe": 1,
                    "factors": [],
                },
            ]
        )
        engine = BacktestEngine(repo)
        config = BacktestConfig(composite="deep_value", filing_lag_days=45, top_k=1)
        signal = engine._get_signal_on_date(config, date(2023, 10, 1))
        assert signal is not None
        snap_date, holdings = signal
        assert snap_date == "2023-08-15"
        assert holdings[0][0] == "WINR"


def test_metrics_golden_values_for_known_returns():
    period_returns = [0.05, 0.05]
    equity_curve = [1.0, 1.05, 1.1025]
    metrics = _compute_metrics_standalone(
        period_returns=period_returns,
        equity_curve=equity_curve,
        periods=[{"turnover": 0.0}, {"turnover": 0.5}],
        start=date(2023, 10, 1),
        end=date(2023, 12, 1),
        rebalance_freq="monthly",
    )
    days = (date(2023, 12, 1) - date(2023, 10, 1)).days
    expected_cagr = (1.1025 ** (365.25 / days)) - 1.0

    assert metrics["finalEquity"] == pytest.approx(1.1025)
    assert metrics["winRate"] == 1.0
    assert metrics["periodsEvaluated"] == 2
    assert metrics["maxDrawdown"] == 0.0
    assert metrics["cagr"] == pytest.approx(expected_cagr, rel=1e-4)
    assert metrics["sharpe"] is None
    assert metrics["calmar"] is None
    assert metrics["turnover"] == pytest.approx(0.25)


def test_metrics_drawdown_and_calmar_for_losing_period():
    metrics = _compute_metrics_standalone(
        period_returns=[-0.10, 0.05],
        equity_curve=[1.0, 0.9, 0.945],
        periods=[{"turnover": None}, {"turnover": 0.0}],
        start=date(2023, 1, 1),
        end=date(2023, 7, 1),
        rebalance_freq="quarterly",
    )
    assert metrics["maxDrawdown"] == pytest.approx(-0.10, rel=1e-4)
    assert metrics["winRate"] == pytest.approx(0.5)
    assert metrics["sharpe"] is not None
    assert metrics["calmar"] is not None
    assert metrics["calmar"] == pytest.approx(metrics["cagr"] / 0.10, rel=0.05)


def test_sharpe_annualization_quarterly_vs_monthly():
    returns = [0.04, -0.02, 0.03, 0.01]
    equity = [1.0]
    for value in returns:
        equity.append(equity[-1] * (1.0 + value))

    monthly = _compute_metrics_standalone(
        period_returns=returns,
        equity_curve=equity,
        periods=[],
        start=date(2022, 1, 1),
        end=date(2023, 1, 1),
        rebalance_freq="monthly",
    )
    quarterly = _compute_metrics_standalone(
        period_returns=returns,
        equity_curve=equity,
        periods=[],
        start=date(2022, 1, 1),
        end=date(2023, 1, 1),
        rebalance_freq="quarterly",
    )
    assert monthly["sharpe"] is not None
    assert quarterly["sharpe"] is not None
    assert monthly["sharpe"] == pytest.approx(quarterly["sharpe"] * math.sqrt(3), rel=0.01)


def test_final_equity_compounds_period_returns(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_backtest_fixture(repo)
        result = BacktestEngine(repo).run(
            BacktestConfig(
                composite="deep_value",
                start_date="2023-10-01",
                end_date="2023-12-01",
                top_k=2,
                filing_lag_days=45,
            )
        )
        period_returns = [period["portfolioReturn"] for period in result.periods if not period["skipped"]]
        compounded = 1.0
        for value in period_returns:
            compounded *= 1.0 + value
        assert result.metrics["finalEquity"] == pytest.approx(compounded, rel=1e-6)
        assert period_returns == [pytest.approx(0.05), pytest.approx(0.05)]


def test_skipped_period_when_no_eligible_snapshot(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies([{"ticker": "WINR", "name": "Winner", "cik": "0000000101"}])
        repo.upsert_prices(
            "WINR",
            [{"date": "2024-06-01", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1}],
            source="test",
        )
        repo.upsert_company_rank_snapshots(
            [
                {
                    "ticker": "WINR",
                    "composite": "deep_value",
                    "snapshot_date": "2024-05-01",
                    "composite_score": 0.9,
                    "rank_in_universe": 1,
                    "factors": [],
                }
            ]
        )
        result = BacktestEngine(repo).run(
            BacktestConfig(
                composite="deep_value",
                start_date="2024-06-01",
                end_date="2024-07-01",
                top_k=1,
                filing_lag_days=45,
            )
        )
        assert result.error == "no_valid_period_returns"
        assert result.periods[0]["skipped"] is True
        assert result.periods[0]["signalDate"] is None


def test_partial_price_coverage_weights_only_valid_tickers(app):
    with app.app_context():
        repo = Repository(get_db())
        repo.upsert_companies(
            [
                {"ticker": "WINR", "name": "Winner", "cik": "0000000101"},
                {"ticker": "NOPX", "name": "No Price", "cik": "0000000102"},
            ]
        )
        repo.upsert_prices(
            "WINR",
            [
                {"date": "2023-10-01", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1},
                {"date": "2023-11-01", "open": 120.0, "high": 120.0, "low": 120.0, "close": 120.0, "volume": 1},
            ],
            source="test",
        )
        repo.upsert_company_rank_snapshots(
            [
                {
                    "ticker": "WINR",
                    "composite": "deep_value",
                    "snapshot_date": "2023-08-15",
                    "composite_score": 0.9,
                    "rank_in_universe": 1,
                    "factors": [],
                },
                {
                    "ticker": "NOPX",
                    "composite": "deep_value",
                    "snapshot_date": "2023-08-15",
                    "composite_score": 0.8,
                    "rank_in_universe": 2,
                    "factors": [],
                },
            ]
        )
        result = BacktestEngine(repo).run(
            BacktestConfig(
                composite="deep_value",
                start_date="2023-10-01",
                end_date="2023-11-01",
                top_k=2,
                filing_lag_days=45,
            )
        )
        assert result.error is None
        assert result.periods[0]["holdings"] == ["WINR"]
        assert result.periods[0]["portfolioReturn"] == pytest.approx(0.20)


def test_quarterly_rebalance_produces_three_month_steps():
    dates = _rebalance_dates(date(2023, 1, 1), date(2023, 10, 1), "quarterly")
    assert dates == [
        date(2023, 1, 1),
        date(2023, 4, 1),
        date(2023, 7, 1),
        date(2023, 10, 1),
    ]


def test_trades_align_with_period_returns(app):
    with app.app_context():
        repo = Repository(get_db())
        _seed_backtest_fixture(repo)
        result = BacktestEngine(repo).run(
            BacktestConfig(
                composite="deep_value",
                start_date="2023-10-01",
                end_date="2023-11-01",
                top_k=2,
                filing_lag_days=45,
            )
        )
        assert result.error is None
        assert len(result.trades) == 2
        for trade in result.trades:
            expected = (trade.exit_price - trade.entry_price) / trade.entry_price
            assert trade.return_pct == pytest.approx(expected, rel=1e-6)


def test_csv_output_columns(app, tmp_path):
    db_path = tmp_path / "test.sqlite3"
    with app.app_context():
        _seed_backtest_fixture(Repository(get_db()))

    from scripts.backtest import main

    csv_file = tmp_path / "bt.csv"
    exit_code = main(
        [
            "--db",
            str(db_path),
            "--start",
            "2023-10-01",
            "--end",
            "2023-11-01",
            "--top-k",
            "1",
            "--csv",
            str(csv_file),
        ]
    )
    assert exit_code == 0
    lines = csv_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "date,ticker,entry_price,exit_price,return_pct"
    assert len(lines) == 2
    assert "WINR" in lines[1]


def test_cli_error_exit_code_for_unknown_composite(tmp_path):
    from scripts.backtest import main

    out_file = tmp_path / "bt.json"
    exit_code = main(
        [
            "--start",
            "2023-10-01",
            "--end",
            "2023-12-01",
            "--composite",
            "bogus_composite",
            "--output",
            str(out_file),
        ]
    )
    assert exit_code == 1
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert "Unknown composite" in payload["error"]
