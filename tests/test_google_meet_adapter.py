import pytest

from meeting_agent.bot.adapters.google_meet import JOIN_BUTTON


@pytest.mark.parametrize(
    "label", ["Ask to join", "Ask to join without camera", "Join now", "Join now without camera"]
)
def test_join_button_matches_meet_labels(label: str) -> None:
    assert JOIN_BUTTON.search(label)


@pytest.mark.parametrize("label", ["Other ways to join", "Switch here"])
def test_join_button_ignores_other_buttons(label: str) -> None:
    assert not JOIN_BUTTON.search(label)
