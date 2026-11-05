"""Build the finished speaker list that clients draw as is."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict, cast

from music_assistant_models.enums import PlayerType

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from music_assistant.models.player import Player

    from . import HassPlayerLocation

# players that render no audio of their own, or that only stand in for another player
_NEVER_LISTED = (PlayerType.SOURCE, PlayerType.PROTOCOL)
# players that can never join a group
_NEVER_GROUPED = (PlayerType.SOURCE, PlayerType.PROTOCOL, PlayerType.UNKNOWN, PlayerType.GROUP)

type SectionKind = Literal["floor", "area", "label", "other", "all"]


class SpeakerListSection(TypedDict):
    """One heading of the speaker list and the rooms under it, in order."""

    key: str
    kind: SectionKind
    # None for "Other" (clients print their own word) and for the single plain block
    title: str | None
    rooms: list[str]


class SpeakerListRoom(TypedDict):
    """A room (player) of the speaker list."""

    name: str
    area: str | None
    floor: str | None
    floor_level: int | None
    labels: list[str]
    available: bool
    # the leader of the group made on the fly that this room is a member of
    group_leader: str | None
    is_permanent_group: bool
    # the rooms a group editor may offer to group with this room, in name order
    group_with: list[str]


class SpeakerListGroup(TypedDict):
    """A group made on the fly, with its colour."""

    leader: str
    colour: str
    members: list[str]


class SpeakerListPermanentGroup(TypedDict):
    """A group player and the rooms it takes out of the list."""

    player: str
    name: str
    members: list[str]


class SpeakerList(TypedDict):
    """The finished speaker list."""

    revision: int
    grouping: Literal["floor", "area", "label", "none"]
    sections: list[SpeakerListSection]
    rooms: dict[str, SpeakerListRoom]
    groups: list[SpeakerListGroup]
    permanent_groups: list[SpeakerListPermanentGroup]


def build_speaker_list(
    *,
    visible: Iterable[Player],
    everyone: Iterable[Player],
    locations: Mapping[str, HassPlayerLocation],
    grouping: Literal["floor", "area", "label", "none"],
    groups: Mapping[str, set[str]],
    colours: Mapping[str, str],
    revision: int,
    own_player_id: str | None = None,
) -> SpeakerList:
    """
    Build the speaker list one user sees.

    :param visible: The players this user may see.
    :param everyone: Every player, whoever asks, to find permanent groups and their rooms.
    :param locations: Where Home Assistant places each player, keyed by player ID.
    :param grouping: How the household wants the list grouped.
    :param groups: The groups made on the fly, as leader ID to member IDs.
    :param colours: The colour of each group made on the fly, keyed by leader ID.
    :param revision: The revision to report, which changes whenever the list may have.
    :param own_player_id: The private client player the caller connected on (such as its
        web player), listed even while hidden, as the client shows it as "this device".
    """
    visible_players = {player.player_id: player for player in visible}
    group_players = [player for player in everyone if player.state.type == PlayerType.GROUP]
    # the rooms a permanent group stands in for never show on their own
    swallowed = {
        member_id
        for group_player in group_players
        for member_id in (
            *group_player.state.group_members,
            *group_player.state.static_group_members,
        )
        if member_id != group_player.player_id
    }
    listed = [
        player
        for player in visible_players.values()
        if player.state.enabled
        and (not player.state.hide_in_ui or player.player_id == own_player_id)
        and player.state.type not in _NEVER_LISTED
        and player.player_id not in swallowed
    ]
    listed_ids = {player.player_id for player in listed}
    leader_of = {
        member_id: leader_id
        for leader_id, member_ids in groups.items()
        for member_id in member_ids
        if leader_id in listed_ids
    }

    def _name_key(player_id: str) -> tuple[str, str]:
        player = visible_players.get(player_id)
        return (_name(player).casefold() if player else "", player_id)

    rooms: dict[str, SpeakerListRoom] = {}
    for player in listed:
        location = locations.get(player.player_id)
        rooms[player.player_id] = {
            "name": _name(player),
            "area": location["area"] if location else None,
            "floor": location["floor"] if location else None,
            "floor_level": location["floor_level"] if location else None,
            "labels": list(location["labels"]) if location else [],
            "available": player.state.available,
            "group_leader": leader_of.get(player.player_id),
            "is_permanent_group": player.state.type == PlayerType.GROUP,
            "group_with": sorted(
                _group_with(player, visible_players, swallowed, groups), key=_name_key
            ),
        }
    return {
        "revision": revision,
        "grouping": grouping,
        "sections": _sections(rooms, grouping, _name_key),
        "rooms": rooms,
        "groups": [
            {
                "leader": leader_id,
                "colour": colours[leader_id],
                "members": sorted((m for m in groups[leader_id] if m in listed_ids), key=_name_key),
            }
            for leader_id in sorted(groups, key=_name_key)
            if leader_id in listed_ids and leader_id in colours
        ],
        "permanent_groups": [
            {
                "player": player.player_id,
                "name": _name(player),
                "members": sorted(
                    {
                        m
                        for m in (*player.state.group_members, *player.state.static_group_members)
                        if m != player.player_id
                    },
                    key=_name_key,
                ),
            }
            for player in sorted(group_players, key=lambda p: _name_key(p.player_id))
            if player.player_id in listed_ids
        ],
    }


def _name(player: Player) -> str:
    return player.state.name or player.player_id


def _group_with(
    leader: Player,
    visible_players: Mapping[str, Player],
    swallowed: set[str],
    groups: Mapping[str, set[str]],
) -> set[str]:
    """
    Return the rooms a group editor may offer to group with the given room.

    A player the leader can group with, enabled and available, not hidden (a hidden
    player stays out of the group editor as it does out of the list), able to join a
    group and not part of another group player. The current members of the leader's
    group are offered too, so they can be taken out again.
    """
    can_group_with = set(leader.state.can_group_with)
    offered = set(groups.get(leader.player_id, ())) & set(visible_players)
    for player_id, player in visible_players.items():
        state = player.state
        if (
            player_id != leader.player_id
            and player_id in can_group_with
            and player_id not in swallowed
            and state.enabled
            and state.available
            and not state.hide_in_ui
            and state.type not in _NEVER_GROUPED
            and (not state.active_group or state.active_group == leader.player_id)
        ):
            offered.add(player_id)
    return offered


def _sections(
    rooms: Mapping[str, SpeakerListRoom],
    grouping: Literal["floor", "area", "label", "none"],
    name_key: Callable[[str], tuple[str, str]],
) -> list[SpeakerListSection]:
    """Return the sections for the given grouping, with "Other" last."""
    by_name = sorted(rooms, key=name_key)
    if grouping == "none" or not any(
        room["area"] or room["floor"] or room["labels"] for room in rooms.values()
    ):
        return [{"key": "all", "kind": "all", "title": None, "rooms": by_name}] if by_name else []
    headed: dict[str, list[str]] = {}
    levels: dict[str, int | None] = {}
    other: list[str] = []
    for room_id in by_name:
        room = rooms[room_id]
        if grouping == "floor":
            names = [room["floor"]] if room["floor"] and room["area"] else []
            if names:
                levels.setdefault(names[0], room["floor_level"])
        elif grouping == "area":
            names = [room["area"]] if room["area"] else []
        else:
            names = list(dict.fromkeys(room["labels"]))
        for name in names:
            headed.setdefault(name, []).append(room_id)
        if not names:
            other.append(room_id)
    if grouping == "floor":
        # highest floor first; on a tie the floor with more rooms, then by name; floors
        # without a level after all the others
        order = sorted(
            headed,
            key=lambda name: (
                levels[name] is None,
                -(levels[name] or 0),
                -len(headed[name]),
                name.casefold(),
            ),
        )
    else:
        order = sorted(headed, key=str.casefold)
    kind = cast("SectionKind", grouping)
    sections: list[SpeakerListSection] = [
        {"key": f"{grouping}:{name.casefold()}", "kind": kind, "title": name, "rooms": headed[name]}
        for name in order
    ]
    if other:
        sections.append({"key": "other", "kind": "other", "title": None, "rooms": other})
    return sections
