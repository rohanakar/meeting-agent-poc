import pytest

from meeting_agent.bot.adapters.base import load_selectors
from meeting_agent.bot.adapters.google_meet import GoogleMeet

JOIN_BUTTON = GoogleMeet.DEFAULT_SELECTORS["join_button"]


@pytest.mark.parametrize(
    "label", ["Ask to join", "Ask to join without camera", "Join now", "Join now without camera"]
)
def test_join_button_matches_meet_labels(label: str) -> None:
    assert JOIN_BUTTON.search(label)


@pytest.mark.parametrize("label", ["Other ways to join", "Switch here"])
def test_join_button_ignores_other_buttons(label: str) -> None:
    assert not JOIN_BUTTON.search(label)


def test_a_selectors_file_replaces_defaults_without_a_rebuild(tmp_path) -> None:
    (tmp_path / "google_meet.json").write_text(
        '{"join_button": "^(Request to join)", "leave_button": "button.leave", "typo": "x"}'
    )

    selectors = load_selectors(tmp_path, "google_meet", GoogleMeet.DEFAULT_SELECTORS)

    assert selectors["join_button"].search("Request to join")
    assert selectors["leave_button"] == "button.leave"
    assert "typo" not in selectors
    assert selectors["chat_input"] == GoogleMeet.DEFAULT_SELECTORS["chat_input"]
