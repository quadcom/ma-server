"""Give every sync group of players a colour that all clients agree on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from music_assistant_models.enums import PlayerType

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from collections.abc import Set as AbstractSet

    from music_assistant.models.player import Player

# The colours groups are named by, in the order new groups take them. Past the last one
# the order starts again, so a seventh group shares the first colour.
GROUP_COLOURS: Final = ("orange", "purple", "green", "pink", "yellow", "teal")


def groups_from_players(players: Iterable[Player]) -> dict[str, set[str]]:
    """
    Return the groups made on the fly among the given players, as leader ID to member IDs.

    A player leads such a group when its own group members list others, or when other
    players are synced to it. The leader itself is never among its members. A player
    with no members leads no group.

    Group players (a permanent sync group, for example) are left out, and so are the
    players that belong to one: such a group has a name of its own and needs no colour.

    :param players: The players to look at, as clients see them.
    """
    player_list = list(players)
    group_player_ids = {p.player_id for p in player_list if p.type == PlayerType.GROUP}
    groups: dict[str, set[str]] = {}
    for player in player_list:
        if player.player_id in group_player_ids:
            continue
        state = player.state
        if members := [m for m in state.group_members if m != player.player_id]:
            groups.setdefault(player.player_id, set()).update(members)
        leader_id = state.synced_to
        if leader_id and leader_id != player.player_id and leader_id not in group_player_ids:
            groups.setdefault(leader_id, set()).add(player.player_id)
    return groups


class GroupColours:
    """
    Keep a colour per sync group for as long as the group lasts.

    A new group takes the lowest free colour. A group keeps its colour while its
    leader stays, and also when a member takes over from a leader that left (as the
    players controller does when the leader of a playing group is removed from it). A
    group that ends frees its colour.
    """

    def __init__(self) -> None:
        """Start with no groups."""
        # colour index per leader; an index past the last colour wraps around
        self._indexes: dict[str, int] = {}
        # the members of each coloured group when it was last seen
        self._members: dict[str, frozenset[str]] = {}

    @property
    def colours(self) -> dict[str, str]:
        """Return the colour of every group, keyed by leader ID."""
        return {
            leader_id: GROUP_COLOURS[index % len(GROUP_COLOURS)]
            for leader_id, index in self._indexes.items()
        }

    def update(self, groups: Mapping[str, AbstractSet[str]]) -> None:
        """
        Bring the colours in line with the groups as they are now.

        :param groups: The current groups, as leader ID to member IDs.
        """
        indexes: dict[str, int] = {}
        # a group whose leader still leads keeps its colour
        for leader_id in groups:
            if leader_id in self._indexes:
                indexes[leader_id] = self._indexes[leader_id]
        # a member that took over from a leader that no longer leads inherits that
        # group's colour; each ended group passes its colour on at most once
        ended = {
            leader_id: members
            for leader_id, members in self._members.items()
            if leader_id not in groups
        }
        for leader_id in sorted(groups):
            if leader_id in indexes:
                continue
            for old_leader_id, old_members in sorted(ended.items()):
                if leader_id in old_members:
                    indexes[leader_id] = self._indexes[old_leader_id]
                    del ended[old_leader_id]
                    break
        # everything else is a new group and takes the lowest free colour, in a fixed
        # order so the same set of groups always ends up with the same colours
        for leader_id in sorted(groups):
            if leader_id in indexes:
                continue
            taken = set(indexes.values())
            index = 0
            while index in taken:
                index += 1
            indexes[leader_id] = index
        self._indexes = indexes
        self._members = {leader_id: frozenset(groups[leader_id]) for leader_id in indexes}
