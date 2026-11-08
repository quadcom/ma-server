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
    group_rooms: dict[str, list[str]] | None = None,
    sides: dict[str, str] | None = None,
    dynamic_groups: set[str] | None = None,
) -> Any:
    return build_speaker_list(
        visible=visible if visible is not None else players,
        everyone=players,
        locations=locations,  # type: ignore[arg-type]
        grouping=grouping,
        groups=groups or {},
        colours=colours or {},
        revision=7,
        group_rooms=group_rooms,
        sides=sides,
        dynamic_groups=frozenset(dynamic_groups or ()),
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
            static_group_members=["bed1", "bed2"],
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
        {
            "player": "bedroom",
            "name": "Master Bedroom",
            "members": ["bed1", "bed2"],
            "is_stereo_pair": False,
            "stereo_left": None,
        }
    ]
    assert result["rooms"]["bedroom"]["is_stereo_pair"] is False


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
    """Offer what the leader can group with, leaving out hidden and unavailable players."""
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
    assert result["rooms"]["kitchen"]["group_with"] == ["deck", "dining"]


def test_the_callers_own_hidden_player_is_listed() -> None:
    """List the caller's own client player while hidden, and no other hidden player."""
    players = [
        _player("mine", hide_in_ui=True, private=True),
        _player("theirs", hide_in_ui=True, private=True),
        _player("off", hide_in_ui=True, private=True, enabled=False),
        _player("kitchen", can_group_with=["mine", "theirs"]),
    ]
    locations = {p.player_id: _at() for p in players}

    result = build_speaker_list(
        visible=players,
        everyone=players,
        locations=locations,  # type: ignore[arg-type]
        grouping="floor",
        groups={},
        colours={},
        revision=1,
        own_player_id="mine",
    )

    assert list(result["rooms"]) == ["mine", "kitchen"]
    # a hidden player is never offered to a group, the caller's own one included
    assert result["rooms"]["kitchen"]["group_with"] == []
    assert build_speaker_list(
        visible=players,
        everyone=players,
        locations=locations,  # type: ignore[arg-type]
        grouping="floor",
        groups={},
        colours={},
        revision=1,
        own_player_id="off",
    )["rooms"].keys() == {"kitchen"}


def _bedroom(**state: Any) -> list[Any]:
    """Return a permanent group of two hidden rooms, like Master Bedroom (MB1 + MB2)."""
    return [
        _player(
            "bedroom",
            "Master Bedroom",
            player_type=PlayerType.GROUP,
            static_group_members=["bed1", "bed2"],
            group_members=["bed1", "bed2"],
        ),
        _player("bed1", "Master Bedroom 1", hide_in_ui=True, **state),
        _player("bed2", "Master Bedroom 2", hide_in_ui=True, **state),
    ]


def test_a_permanent_group_is_offered_as_one_room() -> None:
    """Offer a permanent group whose rooms may all join the leader, never its rooms."""
    players = [
        *_bedroom(),
        _player("bath", can_group_with=["bed1", "bed2", "deck"]),
        _player("deck"),
    ]
    locations = {p.player_id: _at("Room", "Main", 1) for p in players}

    result = _build(players, locations)

    assert result["rooms"]["bath"]["group_with"] == ["deck", "bedroom"]
    # its rooms are never offered back to it
    assert result["rooms"]["bedroom"]["group_with"] == []


def test_a_permanent_group_joins_whole_or_not_at_all() -> None:
    """Leave out a permanent group with an online room the leader can't take."""
    players = [*_bedroom(), _player("bath", can_group_with=["bed1"])]
    locations = {p.player_id: _at() for p in players}

    assert _build(players, locations)["rooms"]["bath"]["group_with"] == []


def test_an_offline_room_does_not_block_its_permanent_group() -> None:
    """Offer a permanent group on its online rooms when one of them is offline."""
    players = [*_bedroom(), _player("bath", can_group_with=["bed1"])]
    players[2].state.available = False
    locations = {p.player_id: _at() for p in players}

    assert _build(players, locations)["rooms"]["bath"]["group_with"] == ["bedroom"]


def test_a_permanent_group_held_elsewhere_is_not_offered() -> None:
    """Leave out a permanent group whose room another group holds."""
    players = [*_bedroom(), _player("bath", can_group_with=["bed1", "bed2"])]
    players[1].state.active_group = "upstairs"
    locations = {p.player_id: _at() for p in players}

    assert _build(players, locations)["rooms"]["bath"]["group_with"] == []


def test_a_permanent_group_playing_on_its_own_is_offered() -> None:
    """Offer a permanent group in its own session, its playing leader judged later."""
    players = _bedroom(active_group="bedroom")
    # core leaves the playing sync leader out of every can_group_with
    players[1].state.group_members = ["bed1", "bed2"]
    players[2].state.synced_to = "bed1"
    players.append(_player("bath", can_group_with=["bed2"]))
    locations = {p.player_id: _at() for p in players}

    result = _build(players, locations, groups={"bed1": {"bed2"}}, colours={"bed1": "green"})

    assert result["rooms"]["bath"]["group_with"] == ["bedroom"]
    # its own session is no group made on the fly
    assert result["groups"] == []
    assert result["rooms"]["bedroom"]["group_leader"] is None


def test_a_permanent_group_in_a_group_shows_as_one_member() -> None:
    """Show a permanent group's rooms in another leader's group as the group itself."""
    players = [*_bedroom(), _player("bath", can_group_with=["deck"]), _player("deck")]
    locations = {p.player_id: _at() for p in players}

    result = _build(
        players, locations, groups={"bath": {"bed1", "bed2", "deck"}}, colours={"bath": "teal"}
    )

    assert result["groups"] == [
        {"leader": "bath", "colour": "teal", "members": ["deck", "bedroom"]}
    ]
    assert result["rooms"]["bedroom"]["group_leader"] == "bath"
    assert "bed1" not in result["rooms"]
    # still on offer, so it can be taken out again
    assert result["rooms"]["bath"]["group_with"] == ["deck", "bedroom"]


def test_a_group_led_by_a_room_of_a_permanent_group_is_led_by_the_group() -> None:
    """Name the permanent group as leader when one of its rooms leads others."""
    players = [*_bedroom(), _player("bath"), _player("deck")]
    locations = {p.player_id: _at() for p in players}

    # the leader of a group holding the permanent group was taken out: core gave bed1 the lead
    result = _build(
        players, locations, groups={"bed1": {"bed2", "bath"}}, colours={"bed1": "purple"}
    )

    assert result["groups"] == [{"leader": "bedroom", "colour": "purple", "members": ["bath"]}]
    assert result["rooms"]["bath"]["group_leader"] == "bedroom"
    assert result["rooms"]["bedroom"]["group_leader"] is None


def test_a_dynamic_group_keeps_the_rooms_it_leads_listed() -> None:
    """Swallow only the rooms a group is set up with, not the ones it leads for now."""
    players = [
        _player(
            "bedroom",
            "Master Bedroom",
            player_type=PlayerType.GROUP,
            group_members=["bed1", "bed2", "bath"],
        ),
        _player("bed1", hide_in_ui=True),
        _player("bed2", hide_in_ui=True),
        _player("bath"),
    ]
    locations = {p.player_id: _at() for p in players}

    result = _build(
        players,
        locations,
        groups={"bed1": {"bed2", "bath"}},
        colours={"bed1": "orange"},
        group_rooms={"bedroom": ["bed1", "bed2"]},
    )

    assert set(result["rooms"]) == {"bath", "bedroom"}
    assert result["permanent_groups"][0]["members"] == ["bed1", "bed2"]
    assert result["rooms"]["bath"]["group_leader"] == "bedroom"


def test_a_group_of_a_left_and_a_right_room_is_a_stereo_pair() -> None:
    """Mark a permanent group of a left and a right room as a stereo pair, naming the left."""
    players = [*_bedroom(), _player("bath", can_group_with=["bed1", "bed2"])]
    locations = {p.player_id: _at() for p in players}

    result = _build(players, locations, sides={"bed1": "right", "bed2": "left"})

    assert result["rooms"]["bedroom"]["is_stereo_pair"] is True
    assert result["permanent_groups"][0]["is_stereo_pair"] is True
    assert result["permanent_groups"][0]["stereo_left"] == "bed2"
    # still one room on offer to other groups
    assert result["rooms"]["bath"]["group_with"] == ["bedroom"]


def test_rooms_on_the_same_side_are_no_stereo_pair() -> None:
    """Need one left and one right room, and only two rooms, for a stereo pair."""
    players = _bedroom()
    locations = {p.player_id: _at() for p in players}

    for sides in ({"bed1": "left", "bed2": "left"}, {"bed1": "left"}, {}):
        result = _build(players, locations, sides=sides)
        assert result["rooms"]["bedroom"]["is_stereo_pair"] is False
        assert result["permanent_groups"][0]["stereo_left"] is None

    trio = [*players, _player("bed3", hide_in_ui=True)]
    trio[0].state.static_group_members = ["bed1", "bed2", "bed3"]
    result = _build(trio, locations, sides={"bed1": "left", "bed2": "right"})
    assert result["rooms"]["bedroom"]["is_stereo_pair"] is False


def test_group_kind_of_each_room() -> None:
    """Name the kind of each group player, and nothing for a speaker of its own."""
    players = [
        *_bedroom(),
        _player("party", "Party", player_type=PlayerType.GROUP, group_members=["bath"]),
        _player("bath"),
    ]
    locations = {p.player_id: _at() for p in players}

    result = _build(players, locations, dynamic_groups={"party"})
    kinds = {room_id: room["group_kind"] for room_id, room in result["rooms"].items()}
    assert kinds == {"bedroom": "permanent", "party": "dynamic", "bath": None}

    paired = _build(players, locations, sides={"bed1": "left", "bed2": "right"})
    assert paired["rooms"]["bedroom"]["group_kind"] == "stereo_pair"
    assert paired["rooms"]["bedroom"]["is_permanent_group"] is True


def test_a_dynamic_group_is_a_named_room_that_keeps_its_rooms_listed() -> None:
    """List a dynamic group as a room of its own, beside its rooms, as no permanent group."""
    players = [
        _player(
            "party",
            "Party",
            player_type=PlayerType.GROUP,
            group_members=["deck", "pool"],
            can_group_with=["deck", "pool", "kitchen"],
        ),
        _player("deck"),
        _player("pool"),
        _player("kitchen", can_group_with=["deck", "pool", "party"]),
    ]
    locations = {p.player_id: _at() for p in players}

    result = _build(players, locations, dynamic_groups={"party"})

    assert set(result["rooms"]) == {"party", "deck", "pool", "kitchen"}
    assert result["rooms"]["party"]["is_permanent_group"] is False
    assert result["permanent_groups"] == []
    # it leads others, but is never offered to join another room's group
    assert result["rooms"]["party"]["group_with"] == ["deck", "kitchen", "pool"]
    assert result["rooms"]["kitchen"]["group_with"] == ["deck", "pool"]


def test_a_playing_dynamic_group_leads_its_coloured_group() -> None:
    """Show the rooms playing with a dynamic group in its group, in its colour."""
    players = [
        _player("party", "Party", player_type=PlayerType.GROUP, group_members=["deck", "pool"]),
        _player("deck", active_group="party"),
        _player("pool", active_group="party"),
    ]
    locations = {p.player_id: _at() for p in players}

    result = _build(
        players,
        locations,
        groups={"party": {"deck", "pool"}},
        colours={"party": "green"},
        dynamic_groups={"party"},
    )

    assert result["groups"] == [{"leader": "party", "colour": "green", "members": ["deck", "pool"]}]
    assert result["rooms"]["deck"]["group_leader"] == "party"


def test_a_permanent_group_can_lead_another_permanent_group() -> None:
    """Offer a permanent group the rooms and permanent groups it can take in as extras."""
    office = [
        _player(
            "office",
            "Office",
            player_type=PlayerType.GROUP,
            static_group_members=["desk1", "desk2"],
            group_members=["desk1", "desk2"],
        ),
        _player("desk1", hide_in_ui=True),
        _player("desk2", hide_in_ui=True),
    ]
    players = [*_bedroom(), *office, _player("deck")]
    players[0].state.can_group_with = ["bed1", "bed2", "desk1", "desk2", "deck"]
    locations = {p.player_id: _at() for p in players}

    result = _build(players, locations)

    assert result["rooms"]["bedroom"]["group_with"] == ["deck", "office"]
