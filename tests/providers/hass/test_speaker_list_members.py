"""Tests for joining speaker-list rooms to a group, a permanent group as one room."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from music_assistant_models.enums import PlayerType
from music_assistant_models.errors import PlayerCommandFailed

from music_assistant.constants import CONF_GROUP_MEMBERS
from music_assistant.providers.hass import HomeAssistantProvider


def _player(player_id: str, player_type: PlayerType = PlayerType.PLAYER, **state: Any) -> Any:
    fields: dict[str, Any] = {
        "available": True,
        "active_group": None,
        "synced_to": None,
        "group_members": [],
        "static_group_members": [],
    }
    fields.update(state)
    return SimpleNamespace(player_id=player_id, state=SimpleNamespace(type=player_type, **fields))


def _provider(players: list[Any], configured: dict[str, list[str]] | None = None) -> Any:
    by_id = {player.player_id: player for player in players}
    provider = HomeAssistantProvider.__new__(HomeAssistantProvider)
    mass_players = MagicMock()
    mass_players.get_player = MagicMock(side_effect=by_id.get)
    mass_players.cmd_set_members = AsyncMock()
    mass_players.cmd_ungroup = AsyncMock()
    config = MagicMock()
    config.get_raw_player_config_value = MagicMock(
        side_effect=lambda player_id, key, default=None: (
            (configured or {}).get(player_id, default) if key == CONF_GROUP_MEMBERS else default
        )
    )
    provider.mass = SimpleNamespace(players=mass_players, config=config)  # type: ignore[assignment]
    provider.logger = logging.getLogger("test.hass")
    return provider


def _bedroom(**state: Any) -> list[Any]:
    return [
        _player("bedroom", PlayerType.GROUP, group_members=["bed1", "bed2"]),
        _player("bed1", **state),
        _player("bed2", **state),
    ]


async def test_a_permanent_group_joins_as_its_rooms() -> None:
    """Add a permanent group's online rooms in its place, plain rooms as they are."""
    players = [*_bedroom(), _player("bath"), _player("deck")]
    players[2].state.available = False
    provider = _provider(players, {"bedroom": ["bed1", "bed2"]})

    await provider.set_speaker_list_members("bath", player_ids_to_add=["bedroom", "deck"])

    provider.mass.players.cmd_set_members.assert_awaited_once_with(
        "bath", player_ids_to_add=["bed1", "deck"], player_ids_to_remove=None
    )
    provider.mass.players.cmd_ungroup.assert_not_awaited()


async def test_a_permanent_group_leaves_as_its_rooms() -> None:
    """Take out all of a permanent group's rooms when it is taken out."""
    provider = _provider([*_bedroom(), _player("bath")], {"bedroom": ["bed1", "bed2"]})

    await provider.set_speaker_list_members("bath", player_ids_to_remove=["bedroom"])

    provider.mass.players.cmd_set_members.assert_awaited_once_with(
        "bath", player_ids_to_add=None, player_ids_to_remove=["bed1", "bed2"]
    )


async def test_a_permanent_group_playing_on_its_own_ends_that_first() -> None:
    """End a permanent group's own session, then add its rooms once they are free."""
    players = [*_bedroom(active_group="bedroom"), _player("bath")]
    provider = _provider(players, {"bedroom": ["bed1", "bed2"]})

    async def _release(_group_id: str) -> None:
        for room in players[1:3]:
            room.state.active_group = None

    provider.mass.players.cmd_ungroup.side_effect = _release

    await provider.set_speaker_list_members("bath", player_ids_to_add=["bedroom"])

    provider.mass.players.cmd_ungroup.assert_awaited_once_with("bedroom")
    provider.mass.players.cmd_set_members.assert_awaited_once_with(
        "bath", player_ids_to_add=["bed1", "bed2"], player_ids_to_remove=None
    )


async def test_nothing_joins_when_the_session_does_not_let_go() -> None:
    """Fail without adding anything when the ended session keeps its rooms."""
    provider = _provider(
        [*_bedroom(active_group="bedroom"), _player("bath")], {"bedroom": ["bed1", "bed2"]}
    )

    with (
        patch("music_assistant.providers.hass.GROUP_RELEASE_TIMEOUT", 0.05),
        pytest.raises(PlayerCommandFailed),
    ):
        await provider.set_speaker_list_members("bath", player_ids_to_add=["bedroom"])

    provider.mass.players.cmd_set_members.assert_not_awaited()


async def test_a_permanent_group_as_leader_is_passed_on_as_is() -> None:
    """Pass a permanent group that leads straight to the stock command."""
    provider = _provider([*_bedroom(), _player("bath")], {"bedroom": ["bed1", "bed2"]})

    await provider.set_speaker_list_members(
        "bedroom", player_ids_to_add=["bath"], player_ids_to_remove=["bedroom"]
    )

    provider.mass.players.cmd_set_members.assert_awaited_once_with(
        "bedroom", player_ids_to_add=["bath"], player_ids_to_remove=["bedroom"]
    )


async def test_a_group_without_settings_falls_back_to_its_members() -> None:
    """Use a group's fixed rooms when its settings name none."""
    players = [
        _player("bedroom", PlayerType.GROUP, static_group_members=["bed1", "bed2"]),
        _player("bed1"),
        _player("bed2"),
        _player("bath"),
    ]
    provider = _provider(players)

    await provider.set_speaker_list_members("bath", player_ids_to_add=["bedroom", "unknown"])

    provider.mass.players.cmd_set_members.assert_awaited_once_with(
        "bath", player_ids_to_add=["bed1", "bed2", "unknown"], player_ids_to_remove=None
    )
