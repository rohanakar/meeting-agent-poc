from dataclasses import dataclass

from meeting_agent.pipeline.speaker_map import map_speakers


@dataclass
class Turn:
    speaker_label: str
    start_ms: int
    end_ms: int


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
