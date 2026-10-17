"""Artist display punctuation must not override structured artist entities."""

import pytest

from applemusic.client import AppleMusicClient


def relationships(*names):
    return [
        {"id": str(index), **({"attributes": {"name": name}} if name else {})}
        for index, name in enumerate(names, 1)
    ]


@pytest.mark.parametrize(
    "display, rel, expected",
    [
        ("HOYO-MiX, Hanser & Sān-Z", relationships(None, None, None), ["HOYO-MiX", "Hanser", "Sān-Z"]),
        ("Sihan, Sān-Z & HOYO-MiX", relationships(None, None, None), ["Sihan", "Sān-Z", "HOYO-MiX"]),
        ("A, B & C", relationships(None, None, None), ["A", "B", "C"]),
        ("A，B 和 C", relationships(None, None, None), ["A", "B", "C"]),
        ("和田光司 & Guest", relationships(None, None), ["和田光司", "Guest"]),
        ("A、和田光司 & C", relationships(None, None, None), ["A", "和田光司", "C"]),
        ("A、B & C", relationships(None, None, None), ["A", "B", "C"]),
        ("A, B and C", relationships(None, None, None), ["A", "B", "C"]),
        ("Tyler, The Creator", relationships(None), ["Tyler, The Creator"]),
        ("Earth, Wind & Fire", relationships(None), ["Earth, Wind & Fire"]),
        ("Simon & Garfunkel", relationships(None), ["Simon & Garfunkel"]),
        ("Tyler, The Creator & Guest", relationships(None, None), ["Tyler, The Creator", "Guest"]),
        ("A, B", relationships(None, None), ["A", "B"]),
        ("A, B, C", relationships(None, None), ["A, B, C"]),
        ("A & B & C", relationships(None, None), ["A & B & C"]),
        ("Tyler, The Creator", [], ["Tyler, The Creator"]),
        ("A, B & C", [], ["A, B", "C"]),
        ("A / B", [], ["A", "B"]),
        ("", relationships("A", "B"), ["A", "B"]),
        ("", relationships("A", None), ["A"]),
        ("", [], []),
        ("Display", relationships("Entity A", "Entity B"), ["Entity A", "Entity B"]),
        ("A, B & C", relationships("A", None, None), ["A", "B", "C"]),
        ("Earth, Wind & Fire & Guest", relationships("Earth, Wind & Fire", None), ["Earth, Wind & Fire", "Guest"]),
        ("Tyler, The Creator, A & B", relationships("Tyler, The Creator", None, None), ["Tyler, The Creator", "A", "B"]),
        ("A & B", relationships("Other A", None), ["A", "B", "Other A"]),
        ("A, B, C", relationships("A", None), ["A, B, C"]),
    ],
)
def test_artist_entities(display, rel, expected):
    assert AppleMusicClient._parse_artists_from_apple(display, rel) == expected


def test_display_order_does_not_reassign_relationship_ids():
    item = {
        "id": "song",
        "attributes": {"name": "Song", "artistName": "B, A & C"},
        "relationships": {"artists": {"data": relationships(None, None, None)}},
    }
    track = AppleMusicClient._parse_song_item(item)
    assert track.artists == ["B", "A", "C"]
    assert track.artist_ids == ["1", "2", "3"]
    assert track.raw_artist_name == "B, A & C"
