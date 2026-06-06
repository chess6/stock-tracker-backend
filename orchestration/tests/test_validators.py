import pytest

from orchestration.services.schemas import ProposedAction
from orchestration.services.validators import ValidationError, validate_action, validate_ticker


def test_validate_ticker():
    assert validate_ticker("jpm") == "JPM"


def test_invalid_ticker():
    with pytest.raises(ValidationError):
        validate_ticker("not a ticker!")


def test_enqueue_job_valid():
    action = ProposedAction(
        action_type="enqueue_job",
        params={"job_type": "refresh_prices", "tickers": ["AAPL"]},
    )
    validated = validate_action(action)
    assert validated.params["tickers"] == ["AAPL"]


def test_disallowed_action():
    with pytest.raises(ValidationError):
        validate_action(ProposedAction(action_type="delete_database"))
