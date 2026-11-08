"""Tests for the colours the Home Assistant provider gives sync groups of players."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from music_assistant_models.enums import PlayerType

from music_assistant.providers.hass.group_colours import GroupColours, groups_from_players


def _player(
    player_id: str,
    group_members: list[str] | None = None,
    synced_to: str | None = None,
    active_group: str | None = None,
    player_type: PlayerType = PlayerType.PLAYER,
) -> Any:
    return SimpleNamespace(
        player_id=player_id,
        type=player_type,
        state=SimpleNamespace(
            group_members=group_members or [],
            synced_to=synced_to,
            active_group=active_group,
        ),
    )


def _colours(*steps: dict[str, set[str]]) -> dict[str, str]:
    group_colours = GroupColours()
    for groups in steps:
        group_colours.update(groups)
    return group_colours.colours


def test_groups_come_from_members_and_from_the_players_synced_to_a_leader() -> None:
    """Find a group from the leader's members or from a player synced to it."""
    players = [
        _player("kitchen", group_members=["kitchen", "dining"]),
        _player("dining", synced_to="kitchen"),
        _player("office", synced_to="den"),
        _player("den"),
        _player("alone", group_members=["alone"]),
    ]

    assert groups_from_players(players) == {"kitchen": {"dining"}, "den": {"office"}}


def test_permanent_group_players_get_no_colour() -> None:
    """Leave out group players and their members: such a group has a name of its own."""
    players = [
        _player("bedroom", group_members=["bed1", "bed2"], player_type=PlayerType.GROUP),
        _player("bed1", active_group="bedroom"),
        _player("bed2", synced_to="bedroom"),
        _player("kitchen", group_members=["kitchen", "dining"]),
        _player("dining", synced_to="kitchen"),
    ]

    assert groups_from_players(players) == {"kitchen": {"dining"}}


def test_a_playing_dynamic_group_leads_the_rooms_playing_with_it() -> None:
    """Make a playing dynamic group the leader of its rooms, not the room leading its session."""
    players = [
        _player("party", group_members=["deck", "pool"], player_type=PlayerType.GROUP),
        _player("deck", group_members=["deck", "pool"], active_group="party"),
        _player("pool", synced_to="deck", active_group="party"),
        _player("kitchen", group_members=["kitchen", "dining"]),
        _player("dining", synced_to="kitchen"),
    ]

    assert groups_from_players(players, {"party"}) == {
        "party": {"deck", "pool"},
        "kitchen": {"dining"},
    }
    # without knowing it is dynamic, it stays a group player with no colour of its own
    assert groups_from_players(players) == {"deck": {"pool"}, "kitchen": {"dining"}}


def test_an_idle_dynamic_group_leads_nothing() -> None:
    """Leave a dynamic group that is not playing out: its members are only its preset."""
    players = [
        _player("party", group_members=["deck", "pool"], player_type=PlayerType.GROUP),
        _player("deck"),
        _player("pool"),
    ]

    assert groups_from_players(players, {"party"}) == {}


def test_new_groups_take_the_colours_in_order() -> None:
    """Give the first group orange and the next ones the following colours."""
    assert _colours({"a": {"a1"}, "b": {"b1"}, "c": {"c1"}}) == {
        "a": "orange",
        "b": "purple",
        "c": "green",
    }


def test_a_group_keeps_its_colour_while_its_leader_stays() -> None:
    """Keep the colour of a group as members join and leave and other groups come along."""
    colours = _colours(
        {"b": {"b1"}},
        {"a": {"a1"}, "b": {"b1"}},
        {"a": {"a1"}, "b": {"b1", "b2"}},
        {"a": {"a1"}, "b": {"b2"}},
    )

    assert colours == {"b": "orange", "a": "purple"}


def test_a_member_taking_over_keeps_the_colour() -> None:
    """Pass the colour on to the member that becomes leader when the leader leaves."""
    colours = _colours(
        {"a": {"a1"}, "b": {"b1", "b2"}},
        {"a": {"a1"}, "b2": {"b1"}},
    )

    assert colours == {"a": "orange", "b2": "purple"}


def test_an_ended_group_frees_its_colour() -> None:
    """Let the next new group reuse the colour of a group that ended."""
    colours = _colours(
        {"a": {"a1"}, "b": {"b1"}, "c": {"c1"}},
        {"a": {"a1"}, "c": {"c1"}},
        {"a": {"a1"}, "c": {"c1"}, "d": {"d1"}},
    )

    assert colours == {"a": "orange", "c": "green", "d": "purple"}


def test_new_groups_take_the_lowest_free_colours() -> None:
    """Fill the gaps from the lowest colour up when several groups start at once."""
    colours = _colours(
        {"a": {"a1"}, "b": {"b1"}, "c": {"c1"}, "d": {"d1"}},
        {"c": {"c1"}},
        {"c": {"c1"}, "e": {"e1"}, "f": {"f1"}, "g": {"g1"}},
    )

    assert colours == {"c": "green", "e": "orange", "f": "purple", "g": "pink"}


def test_the_seventh_group_starts_the_colours_again() -> None:
    """Wrap around past the last colour, so the seventh group is orange again."""
    colours = _colours({leader: {f"{leader}1"} for leader in "abcdefg"})

    assert list(colours.values()) == [
        "orange",
        "purple",
        "green",
        "pink",
        "yellow",
        "teal",
        "orange",
    ]


def test_an_ended_group_passes_its_colour_on_only_once() -> None:
    """Give only one of two members that each lead a group of their own the old colour."""
    colours = _colours(
        {"a": {"a1", "a2"}},
        {"a1": {"x"}, "a2": {"y"}},
    )

    assert colours == {"a1": "orange", "a2": "purple"}


def test_no_groups_no_colours() -> None:
    """Report no colours once every group has ended."""
    assert _colours({"a": {"a1"}}, {}) == {}
