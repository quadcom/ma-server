"""Tests for the finished speaker list the Home Assistant provider builds."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from music_assistant_models.enums import PlayerType

from music_assistant.providers.hass.speaker_list import build_speaker_list


def _player(
    player_id: str,
    name: str | None = None,
    player_type: PlayerType = PlayerType.PLAYER,
    **state: Any,
) -> Any:
    fields: dict[str, Any] = {
        "enabled": True,
        "available": True,
        "hide_in_ui": False,
        "private": False,
        "group_members": [],
        "static_group_members": [],
        "synced_to": None,
        "active_group": None,
        "can_group_with": [],
    }
    fields.update(state)
    return SimpleNamespace(
        player_id=player_id,
        state=SimpleNamespace(name=name or player_id.title(), type=player_type, **fields),
    )


def _at(
    area: str | None = None,
    floor: str | None = None,
    level: int | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return {"area": area, "floor": floor, "floor_level": level, "labels": labels or []}


def _build(
    players: list[Any],
    locations: dict[str, dict[str, Any]],
    grouping: Any = "floor",
    *,
    visible: list[Any] | None = None,
    groups: dict[str, set[str]] | None = None,
    colours: dict[str, str] | None = None,
) -> Any:
    return build_speaker_list(
        visible=visible if visible is not None else players,
        everyone=players,
        locations=locations,  # type: ignore[arg-type]
        grouping=grouping,
        groups=groups or {},
        colours=colours or {},
        revision=7,
    )


def _sections(result: Any) -> list[tuple[str, str | None, list[str]]]:
    return [(s["kind"], s["title"], s["rooms"]) for s in result["sections"]]


def test_floors_highest_first_with_other_last() -> None:
    """Sort floors from the highest level down, rooms by name, and put "Other" last."""
    players = [_player("gym"), _player("kitchen"), _player("deck"), _player("phone")]
    locations = {
        "gym": _at("Gym", "Basement", -1),
        "kitchen": _at("Kitchen", "Main", 1),
        "deck": _at("Deck", "Main", 1),
        "phone": _at(),
    }

    result = _build(players, locations)

    assert result["revision"] == 7
    assert result["grouping"] == "floor"
    assert _sections(result) == [
        ("floor", "Main", ["deck", "kitchen"]),
        ("floor", "Basement", ["gym"]),
        ("other", None, ["phone"]),
    ]


def test_floor_ties_go_to_more_rooms_then_name() -> None:
    """Break a tie in level by the number of rooms, then by name, unlevelled floors last."""
    players = [_player(p) for p in ("a", "b", "c", "d", "e")]
    locations = {
        "a": _at("A", "Garage", 1),
        "b": _at("B", "Main", 1),
        "c": _at("C", "Main", 1),
        "d": _at("D", "attic", 1),
        "e": _at("E", "Loft"),
    }

    result = _build(players, locations)

    assert [title for _, title, _ in _sections(result)] == ["Main", "attic", "Garage", "Loft"]


def test_a_room_without_an_area_is_other_when_grouped_by_floor() -> None:
    """Put a room with a floor but no area under "Other", as the apps did."""
    players = [_player("kitchen"), _player("odd")]
    locations = {"kitchen": _at("Kitchen", "Main", 1), "odd": _at(None, "Main", 1)}

    assert _sections(_build(players, locations))[-1] == ("other", None, ["odd"])


def test_areas_a_to_z() -> None:
    """Head each area, A to Z regardless of case, with rooms without one under "Other"."""
    players = [_player("k"), _player("b"), _player("x")]
    locations = {"k": _at("kitchen", "Main", 1), "b": _at("Bedroom", "Main", 1), "x": _at()}

    assert _sections(_build(players, locations, "area")) == [
        ("area", "Bedroom", ["b"]),
        ("area", "kitchen", ["k"]),
        ("other", None, ["x"]),
    ]


def test_a_room_repeats_under_each_of_its_labels() -> None:
    """List a room under every label it carries, once in the rooms."""
    players = [_player("deck"), _player("gym")]
    locations = {"deck": _at(labels=["Party", "Outside"]), "gym": _at(labels=["Party"])}

    result = _build(players, locations, "label")

    assert _sections(result) == [
        ("label", "Outside", ["deck"]),
        ("label", "Party", ["deck", "gym"]),
    ]
    assert list(result["rooms"]) == ["deck", "gym"]


def test_no_grouping_is_one_plain_block() -> None:
    """Give one section without a heading when the household groups by nothing."""
    players = [_player("b"), _player("a")]
    locations = {"a": _at("A", "Main", 1), "b": _at("B", "Main", 1)}

    assert _sections(_build(players, locations, "none")) == [("all", None, ["a", "b"])]


def test_no_locations_at_all_is_one_plain_block() -> None:
    """Give one plain block when Home Assistant places no room anywhere."""
    players = [_player("b"), _player("a")]

    assert _sections(_build(players, {"a": _at(), "b": _at()})) == [("all", None, ["a", "b"])]


def test_left_out_players() -> None:
    """Leave out hidden, disabled, protocol and source players, but keep unavailable ones."""
    players = [
        _player("hidden", hide_in_ui=True),
        _player("off", enabled=False),
        _player("proto", player_type=PlayerType.PROTOCOL),
        _player("input", player_type=PlayerType.SOURCE),
        _player("asleep", available=False),
    ]
    locations = {p.player_id: _at("Den", "Main", 1) for p in players}

    result = _build(players, locations)

    assert list(result["rooms"]) == ["asleep"]
    assert result["rooms"]["asleep"]["available"] is False


def test_only_rooms_the_user_may_see() -> None:
    """List only the players this user may see."""
    players = [_player("mine"), _player("theirs")]
    locations = {"mine": _at("A", "Main", 1), "theirs": _at("B", "Main", 1)}

    result = _build(players, locations, visible=[players[0]])

    assert list(result["rooms"]) == ["mine"]


def test_a_permanent_group_is_one_room_without_its_members() -> None:
    """List a group player as a room of its own and leave out the rooms it stands in for."""
    players = [
        _player(
            "bedroom",
            "Master Bedroom",
            player_type=PlayerType.GROUP,
            group_members=["bed1"],
            static_group_members=["bed2"],
        ),
        _player("bed1", "Master Bedroom 1", hide_in_ui=True),
        _player("bed2", "Master Bedroom 2"),
        _player("kitchen"),
    ]
    locations = {
        "bedroom": _at("Bedroom", "Main", 1),
        "bed1": _at("Bedroom", "Main", 1),
        "bed2": _at("Bedroom", "Main", 1),
        "kitchen": _at("Kitchen", "Main", 1),
    }

    result = _build(players, locations)

    assert _sections(result) == [("floor", "Main", ["kitchen", "bedroom"])]
    assert result["rooms"]["bedroom"]["is_permanent_group"] is True
    assert result["permanent_groups"] == [
        {"player": "bedroom", "name": "Master Bedroom", "members": ["bed1", "bed2"]}
    ]


def test_groups_made_on_the_fly_grey_their_members() -> None:
    """Hand out each group with its colour, and point its members at their leader."""
    players = [_player("kitchen"), _player("dining"), _player("deck"), _player("gym")]
    locations = {p.player_id: _at("Room", "Main", 1) for p in players}

    result = _build(
        players,
        locations,
        groups={"kitchen": {"dining", "deck"}},
        colours={"kitchen": "orange"},
    )

    assert result["groups"] == [
        {"leader": "kitchen", "colour": "orange", "members": ["deck", "dining"]}
    ]
    assert result["rooms"]["dining"]["group_leader"] == "kitchen"
    assert result["rooms"]["kitchen"]["group_leader"] is None
    assert result["rooms"]["gym"]["group_leader"] is None


def test_group_with_follows_the_stock_group_editor() -> None:
    """Offer what the leader can group with, minus what the stock editor would not offer."""
    players = [
        _player(
            "kitchen",
            can_group_with=["dining", "gone", "hidden", "secret", "input", "busy", "self"],
        ),
        _player("dining"),
        _player("gone", available=False),
        _player("hidden", hide_in_ui=True),
        _player("secret", hide_in_ui=True, private=True),
        _player("input", player_type=PlayerType.SOURCE),
        _player("busy", active_group="elsewhere"),
        _player("deck"),
    ]
    locations = {p.player_id: _at("Room", "Main", 1) for p in players}

    result = _build(players, locations, groups={"kitchen": {"deck"}}, colours={"kitchen": "pink"})

    # the current member stays on offer so it can be taken out again
    assert result["rooms"]["kitchen"]["group_with"] == ["deck", "dining", "hidden"]
