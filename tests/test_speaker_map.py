from dataclasses import dataclass

from meeting_agent.pipeline.speaker_map import map_speakers, name_turns


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


def test_a_turn_whose_own_captions_show_someone_else_gets_their_name() -> None:
    # ElevenLabs gave Carl's voice Anna's label. Ben's "Yeah" is too short for captions of its
    # own: the marks in it are Carl's captions catching up.
    turns = [
        Turn("Speaker 1", 0, 6000),  # Anna
        Turn("Speaker 2", 6500, 16000),  # Ben
        Turn("Speaker 1", 16500, 19000),  # Carl
        Turn("Speaker 2", 19000, 19300),  # Ben: "Yeah"
        Turn("Speaker 1", 19500, 25000),  # Anna
    ]
    # Captions trail speech by about a second.
    marks = [
        {"t_ms": t, "name": name}
        for name, times in (
            ("Anna", range(1000, 7000, 500)),
            ("Ben", range(7500, 17000, 500)),
            ("Carl", range(17500, 20500, 500)),
            ("Anna", range(20500, 26000, 500)),
        )
        for t in times
    ]
    attendees = [{"name": "Carl", "email": "carl@client.com"}]

    matches = map_speakers(turns, marks, attendees)
    names = name_turns(turns, marks, matches, attendees)

    assert matches["Speaker 1"].name == "Anna"
    assert [match.name for match in names] == ["Anna", "Ben", "Carl", "Ben", "Anna"]
    assert names[2].email == "carl@client.com"
