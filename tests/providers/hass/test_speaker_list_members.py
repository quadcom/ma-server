"""Tests for the speaker list's group commands: a permanent group as one room, stereo pairs."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from music_assistant_models.enums import PlayerType
from music_assistant_models.errors import InvalidDataError, PlayerCommandFailed

from music_assistant.constants import (
    CONF_GROUP_MEMBERS,
    CONF_OUTPUT_CHANNELS,
    CONF_PREFERRED_OUTPUT_PROTOCOL,
)
from music_assistant.providers.hass import IDENTIFY_SOUND_PATH, HomeAssistantProvider


def _player(player_id: str, player_type: PlayerType = PlayerType.PLAYER, **state: Any) -> Any:
    fields: dict[str, Any] = {
        "available": True,
        "active_group": None,
        "synced_to": None,
        "group_members": [],
        "static_group_members": [],
    }
    outputs = state.pop("outputs", [])
    fields.update(state)
    return SimpleNamespace(
        player_id=player_id,
        state=SimpleNamespace(type=player_type, **fields),
        output_protocols=outputs,
    )


def _output(output_id: str, domain: str, *, native: bool = False, available: bool = True) -> Any:
    return SimpleNamespace(
        output_protocol_id=output_id, protocol_domain=domain, is_native=native, available=available
    )


def _sonos(player_id: str, *, sendspin: bool = True) -> Any:
    outputs = [_output("native", "sonos", native=True), _output(f"ap_{player_id}", "airplay")]
    if sendspin:
        outputs.append(_output(f"spb_{player_id}", "sendspin"))
    return _player(player_id, outputs=outputs)


def _provider(players: list[Any], configured: dict[str, list[str]] | None = None) -> Any:
    by_id = {player.player_id: player for player in players}
    provider = HomeAssistantProvider.__new__(HomeAssistantProvider)
    mass_players = MagicMock()
    mass_players.get_player = MagicMock(side_effect=by_id.get)
    mass_players.cmd_set_members = AsyncMock()
    mass_players.cmd_ungroup = AsyncMock()
    config = MagicMock()
    config.save_player_config = AsyncMock()
    config.get_raw_player_config_value = MagicMock(
        side_effect=lambda player_id, key, default=None: (
            (configured or {}).get(player_id, default) if key == CONF_GROUP_MEMBERS else default
        )
    )
    provider.mass = SimpleNamespace(  # type: ignore[assignment]
        players=mass_players,
        config=config,
        streams=SimpleNamespace(base_url="http://ma.local:8097"),
        create_task=MagicMock(side_effect=lambda coro: coro.close()),
    )
    provider.logger = logging.getLogger("test.hass")
    provider._speaker_list_changed = MagicMock()  # type: ignore[method-assign]
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


def _pair(**bed1: Any) -> Any:
    players = [
        _player("bedroom", PlayerType.GROUP),
        _sonos("bed1", **bed1),
        _sonos("bed2"),
    ]
    return _provider(players, {"bedroom": ["bed1", "bed2"]})


async def test_a_stereo_pair_plays_each_side_over_sendspin() -> None:
    """Set the left and right speaker, each over its own Sendspin output."""
    provider = _pair()

    await provider.set_stereo_pair("bedroom", "bed2")

    saved = provider.mass.config.save_player_config.await_args_list
    assert [c.args for c in saved] == [
        ("bed1", {CONF_OUTPUT_CHANNELS: "right", CONF_PREFERRED_OUTPUT_PROTOCOL: "spb_bed1"}),
        ("spb_bed1", {CONF_OUTPUT_CHANNELS: "right"}),
        ("bed2", {CONF_OUTPUT_CHANNELS: "left", CONF_PREFERRED_OUTPUT_PROTOCOL: "spb_bed2"}),
        ("spb_bed2", {CONF_OUTPUT_CHANNELS: "left"}),
    ]
    provider._speaker_list_changed.assert_called_once_with()


async def test_turning_a_stereo_pair_off_puts_both_back_to_stereo() -> None:
    """Put both speakers back to stereo over their own output."""
    provider = _pair()

    await provider.set_stereo_pair("bedroom", None)

    saved = provider.mass.config.save_player_config.await_args_list
    assert [c.args for c in saved] == [
        ("bed1", {CONF_OUTPUT_CHANNELS: "stereo", CONF_PREFERRED_OUTPUT_PROTOCOL: "native"}),
        ("spb_bed1", {CONF_OUTPUT_CHANNELS: "stereo"}),
        ("bed2", {CONF_OUTPUT_CHANNELS: "stereo", CONF_PREFERRED_OUTPUT_PROTOCOL: "native"}),
        ("spb_bed2", {CONF_OUTPUT_CHANNELS: "stereo"}),
    ]


async def test_a_speaker_without_sendspin_cannot_play_one_side() -> None:
    """Refuse a pair when a speaker has no Sendspin output, saving nothing."""
    provider = _pair(sendspin=False)

    with pytest.raises(PlayerCommandFailed):
        await provider.set_stereo_pair("bedroom", "bed1")

    provider.mass.config.save_player_config.assert_not_awaited()


@pytest.mark.parametrize(
    ("group_id", "left"),
    [("bed1", "bed1"), ("bedroom", "kitchen"), ("trio", "bed1")],
)
async def test_a_stereo_pair_needs_a_group_of_two(group_id: str, left: str) -> None:
    """Refuse a speaker, a left speaker outside the group, or a group of three."""
    players = [
        _player("bedroom", PlayerType.GROUP),
        _player("trio", PlayerType.GROUP),
        _sonos("bed1"),
        _sonos("bed2"),
        _sonos("bed3"),
    ]
    provider = _provider(players, {"bedroom": ["bed1", "bed2"], "trio": ["bed1", "bed2", "bed3"]})

    with pytest.raises(InvalidDataError):
        await provider.set_stereo_pair(group_id, left)

    provider.mass.config.save_player_config.assert_not_awaited()


async def test_identify_plays_a_sound_on_one_speaker() -> None:
    """Send the identify sound to the one speaker, without waiting for it to play."""
    provider = _pair()

    await provider.identify_stereo_speaker("bed1")

    provider.mass.create_task.assert_called_once()
    provider.mass.players.play_announcement.assert_called_once_with(
        "bed1", url=f"http://ma.local:8097{IDENTIFY_SOUND_PATH}", pre_announce=False
    )


async def test_identify_needs_an_available_speaker() -> None:
    """Refuse to identify a speaker that is offline or unknown."""
    provider = _pair()
    provider.mass.players.get_player("bed1").state.available = False

    for player_id in ("bed1", "nowhere"):
        with pytest.raises(PlayerCommandFailed):
            await provider.identify_stereo_speaker(player_id)
    provider.mass.create_task.assert_not_called()
