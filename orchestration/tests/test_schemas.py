from orchestration.services.schemas import AgentOutput, EventType, ProposedAction


def test_agent_output_validates():
    out = AgentOutput(
        agent="TestAgent",
        confidence=0.9,
        summary="test",
        proposed_actions=[ProposedAction(action_type="no_op")],
    )
    assert out.agent == "TestAgent"


def test_event_priorities_ordering():
    from orchestration.services.schemas import EVENT_PRIORITIES

    assert EVENT_PRIORITIES[EventType.HIGH_PRIORITY_SIGNAL] < EVENT_PRIORITIES[EventType.NEWS_INGESTED]
