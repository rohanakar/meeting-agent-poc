from dataclasses import dataclass

from meeting_agent.bot.speakers import parse_captions, speaking_now
from meeting_agent.pipeline.speaker_map import map_speakers, read_attendees


@dataclass
class Turn:
    speaker_label: str
    start_ms: int
    end_ms: int


def test_captions_split_into_speaker_blocks_by_known_names() -> None:
    text = "Anna Kumar\nHello everyone, shall we start?\nYou\nThis meeting is recorded.\nBen\nYes"

    blocks = parse_captions(text, ["Anna Kumar", "Ben"])

    assert blocks == [
        ("Anna Kumar", "Hello everyone, shall we start?"),
        ("You", "This meeting is recorded."),
        ("Ben", "Yes"),
    ]


def test_without_known_names_short_title_case_lines_are_speakers() -> None:
    assert parse_captions("Anna Kumar\nhello there.", []) == [("Anna Kumar", "hello there.")]


def test_a_full_name_missing_from_the_tiles_still_starts_a_block() -> None:
    text = "Vamshi Gangadhar\nsounds good\nRishabh C\nlet us send it\nOkay"

    assert parse_captions(text, ["Vamshi Gangadhar"]) == [
        ("Vamshi Gangadhar", "sounds good"),
        ("Rishabh C", "let us send it Okay"),
    ]


def test_names_differing_only_in_case_stay_two_people() -> None:
    text = "rishabh Chaudhary\nlet us start\nRishabh Chaudhary\nnoted"

    assert parse_captions(text, ["Rishabh Chaudhary", "rishabh Chaudhary"]) == [
        ("rishabh Chaudhary", "let us start"),
        ("Rishabh Chaudhary", "noted"),
    ]


def test_only_new_or_growing_blocks_are_speaking() -> None:
    before = [("Anna", "Hello"), ("Ben", "Yes")]
    after = [("Anna", "Hello"), ("Ben", "Yes I agree")]

    assert speaking_now(before, after) == {"Ben"}


def test_each_label_gets_the_name_with_most_caption_marks_and_its_email() -> None:
    turns = [
        Turn("Speaker 1", 0, 4000),
        Turn("Speaker 2", 5000, 8000),
        Turn("Speaker 1", 9000, 9500),
    ]
    # Captions trail speech: Anna's marks run up to 2 s after her turn ends.
    marks = [{"t_ms": t, "name": "Anna"} for t in (1000, 2500, 4500, 10500)] + [
        {"t_ms": t, "name": "Ben"} for t in (6000, 7000, 8500)
    ]
    attendees = [{"name": "anna", "email": "anna@client.com"}, {"name": "Ben", "email": None}]

    matches = map_speakers(turns, marks, attendees)

    assert (matches["Speaker 1"].name, matches["Speaker 1"].email) == ("Anna", "anna@client.com")
    assert (matches["Speaker 2"].name, matches["Speaker 2"].email) == ("Ben", None)


def test_a_label_without_a_clear_majority_stays_anonymous() -> None:
    turns = [Turn("Speaker 1", 0, 4000)]
    marks = [
        {"t_ms": 1000, "name": "Anna"},
        {"t_ms": 2000, "name": "Ben"},
        {"t_ms": 3000, "name": "Cy"},
    ]

    assert map_speakers(turns, marks, []) == {}


def test_attendees_from_older_runs_are_bare_names() -> None:
    assert read_attendees(["Anna", {"name": "Ben", "email": "b@x.com"}]) == [
        {"name": "Anna", "email": None},
        {"name": "Ben", "email": "b@x.com"},
    ]
