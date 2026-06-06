from __future__ import annotations

from unittest.mock import patch

import api_tester


def test_tickers_arg_splits_and_uppercases():
    assert api_tester._tickers_arg("jpm, mcd") == ["JPM", "MCD"]


@patch("api_tester._request")
def test_cmd_financials_summary_mode(mock_request):
    mock_request.return_value = (
        200,
        {
            "metrics": {
                "JPM": {"marketCap": 1, "sp": 2, "ebitdaEv": None},
            }
        },
    )
    args = api_tester.argparse.Namespace(
        tickers="JPM",
        dimension="MRY",
        gte=None,
        most_recent=True,
        summary=True,
    )
    assert api_tester.cmd_financials("http://localhost:5000/api", args) == 0
    mock_request.assert_called_once()
