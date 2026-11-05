"""Tests for looking up where Music Assistant players are placed in Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from music_assistant_models.enums import PlayerType
from music_assistant_models.errors import ProviderUnavailableError

from music_assistant.providers.hass import HomeAssistantProvider
from music_assistant.providers.hass.constants import CONF_PLAYER_GROUPING

AREAS = [
    {"area_id": "kitchen", "name": "Kitchen", "floor_id": "ground"},
    {"area_id": "office", "name": "Office", "floor_id": "upstairs"},
    {"area_id": "garage", "name": "Garage", "floor_id": None},
]
FLOORS = [
    {"floor_id": "ground", "name": "Ground floor", "level": 0},
    {"floor_id": "upstairs", "name": "Upstairs", "level": 1},
]
LABELS = [
    {"label_id": "party", "name": "Party"},
    {"label_id": "downstairs", "name": "downstairs speakers"},
    {"label_id": "quiet", "name": "Quiet"},
]


class _Cache:
    """Provide the slice of the cache controller that @use_cache relies on."""

    def __init__(self) -> None:
        self.entries: dict[str, Any] = {}

    async def get_with_freshness(self, key: str, **kwargs: Any) -> tuple[Any, bool, bool]:
        """Return the (data, is_fresh, found) triplet for the given key."""
        await asyncio.sleep(0)
        if key not in self.entries:
            return None, False, False
        return self.entries[key], True, True

    async def set(self, key: str, data: Any, **kwargs: Any) -> None:
        """Store data under the given key."""
        self.entries[key] = data


def _device(device_id: str, player_id: str | None, **fields: Any) -> dict[str, Any]:
    identifiers = [["music_assistant", player_id]] if player_id else [["cast", device_id]]
    return {"id": device_id, "identifiers": identifiers, "area_id": None, "labels": [], **fields}


def _entity(
    entity_id: str, device_id: str, platform: str = "music_assistant", **fields: Any
) -> dict[str, Any]:
    return {"ei": entity_id, "pl": platform, "di": device_id, "lb": [], **fields}


def _provider(
    player_ids: list[str],
    devices: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    *,
    connected: bool = True,
    config_values: dict[str, Any] | None = None,
    all_players: list[Any] | None = None,
) -> HomeAssistantProvider:
    async def _send_command(command: str, **_kwargs: Any) -> Any:
        if command == "config/entity_registry/list_for_display":
            return {"entity_categories": {}, "entities": entities}
        if command == "config/floor_registry/list":
            return FLOORS
        if command == "config/label_registry/list":
            return LABELS
        raise AssertionError(f"unexpected command {command}")

    provider = HomeAssistantProvider.__new__(HomeAssistantProvider)
    provider.hass = SimpleNamespace(
        connected=connected,
        get_device_registry=AsyncMock(return_value=devices),
        get_area_registry=AsyncMock(return_value=AREAS),
        send_command=AsyncMock(side_effect=_send_command),
    )
    provider._entity_registry = None
    provider._entity_registry_lock = asyncio.Lock()
    # the registry lookups run through @use_cache, which needs a cache to talk to
    values = config_values or {}
    provider.config = SimpleNamespace(  # type: ignore[assignment]
        instance_id="hass--test",
        get_value=lambda key, default=None: values.get(key, default),
    )
    provider.manifest = SimpleNamespace(domain="hass")  # type: ignore[assignment]
    players = MagicMock()
    players.all_players = MagicMock(
        return_value=[SimpleNamespace(player_id=player_id) for player_id in player_ids]
    )
    # every player regardless of the user, from which the group colours are worked out
    players.iter_players = MagicMock(return_value=all_players or [])
    provider.mass = SimpleNamespace(  # type: ignore[assignment]
        cache=_Cache(),
        players=players,
        create_task=lambda coro, **_kwargs: asyncio.ensure_future(coro),
    )
    provider.logger = logging.getLogger("test.hass")
    return provider


async def test_area_and_floor_come_from_the_device() -> None:
    """Report the device's area, and that area's floor, for a player without its own area."""
    provider = _provider(
        ["player1"],
        [_device("dev1", "player1", area_id="kitchen")],
        [_entity("media_player.kitchen", "dev1")],
    )

    result = await provider.get_player_locations()

    assert result == {
        "grouping": "none",
        "players": {
            "player1": {
                "area": "Kitchen",
                "floor": "Ground floor",
                "floor_level": 0,
                "labels": [],
            }
        },
        "group_colours": {},
    }


async def test_area_of_the_entity_overrides_the_device() -> None:
    """Prefer the area assigned to the media_player entity over the one of its device."""
    provider = _provider(
        ["player1"],
        [_device("dev1", "player1", area_id="kitchen")],
        [_entity("media_player.kitchen", "dev1", ai="office")],
    )

    result = await provider.get_player_locations()

    location = result["players"]["player1"]
    assert (location["area"], location["floor"], location["floor_level"]) == (
        "Office",
        "Upstairs",
        1,
    )


async def test_area_without_a_floor() -> None:
    """Report no floor for an area that is not on one."""
    provider = _provider(
        ["player1"],
        [_device("dev1", "player1", area_id="garage")],
        [_entity("media_player.garage", "dev1")],
    )

    location = (await provider.get_player_locations())["players"]["player1"]

    assert (location["area"], location["floor"], location["floor_level"]) == (
        "Garage",
        None,
        None,
    )


async def test_player_without_an_area() -> None:
    """Report nothing for a player Home Assistant has not placed anywhere."""
    provider = _provider(
        ["player1"], [_device("dev1", "player1")], [_entity("media_player.phone", "dev1")]
    )

    location = (await provider.get_player_locations())["players"]["player1"]

    assert location == {"area": None, "floor": None, "floor_level": None, "labels": []}


async def test_player_without_a_device_in_home_assistant() -> None:
    """List a player Home Assistant does not know about, with nothing filled in."""
    provider = _provider(["player1", "unknown"], [_device("dev1", "player1")], [])

    result = await provider.get_player_locations()

    assert result["players"]["unknown"] == {
        "area": None,
        "floor": None,
        "floor_level": None,
        "labels": [],
    }


async def test_labels_of_entity_and_device_are_merged_and_sorted() -> None:
    """Merge the entity's and the device's labels, without duplicates, sorted by name."""
    provider = _provider(
        ["player1"],
        [_device("dev1", "player1", labels=["party", "downstairs"])],
        [_entity("media_player.kitchen", "dev1", lb=["quiet", "party"])],
    )

    location = (await provider.get_player_locations())["players"]["player1"]

    assert location["labels"] == ["downstairs speakers", "Party", "Quiet"]


async def test_unknown_label_is_left_out() -> None:
    """Leave out a label id the label registry does not know, rather than show the id."""
    provider = _provider(
        ["player1"],
        [_device("dev1", "player1", labels=["deleted_label"])],
        [_entity("media_player.kitchen", "dev1", lb=["party"])],
    )

    location = (await provider.get_player_locations())["players"]["player1"]

    assert location["labels"] == ["Party"]


async def test_only_the_music_assistant_media_player_counts() -> None:
    """Ignore other entities on the device and other integrations' devices for the hardware."""
    provider = _provider(
        ["player1"],
        [
            _device("dev1", "player1", area_id="kitchen"),
            # the same speaker as exposed by another integration, placed elsewhere
            _device("cast1", None, area_id="office", labels=["quiet"]),
        ],
        [
            _entity("media_player.kitchen", "dev1"),
            # a button of the Music Assistant integration on the same device
            _entity("button.kitchen_favorite", "dev1", ai="garage", lb=["party"]),
            _entity("media_player.kitchen_cast", "cast1", platform="cast", ai="office"),
        ],
    )

    location = (await provider.get_player_locations())["players"]["player1"]

    assert location["area"] == "Kitchen"
    assert location["labels"] == []


async def test_disconnected_home_assistant_raises() -> None:
    """Raise when Home Assistant is not connected, instead of reporting empty locations."""
    provider = _provider(["player1"], [], [], connected=False)

    with pytest.raises(ProviderUnavailableError):
        await provider.get_player_locations()


async def test_grouping_defaults_to_none() -> None:
    """Hand out no grouping while the household has not picked one."""
    provider = _provider(["player1"], [], [])

    assert (await provider.get_player_locations())["grouping"] == "none"


@pytest.mark.parametrize("grouping", ["floor", "area", "label", "none"])
async def test_grouping_is_handed_out_as_configured(grouping: str) -> None:
    """Hand out the grouping picked in the provider settings."""
    provider = _provider(["player1"], [], [], config_values={CONF_PLAYER_GROUPING: grouping})

    assert (await provider.get_player_locations())["grouping"] == grouping


async def test_unknown_grouping_is_handed_out_as_none() -> None:
    """Hand out none for a stored grouping that clients would not know."""
    provider = _provider(["player1"], [], [], config_values={CONF_PLAYER_GROUPING: "room"})

    assert (await provider.get_player_locations())["grouping"] == "none"


def _grouped_player(
    player_id: str, group_members: list[str] | None = None, synced_to: str | None = None
) -> Any:
    return SimpleNamespace(
        player_id=player_id,
        type=PlayerType.PLAYER,
        state=SimpleNamespace(
            group_members=group_members or [], synced_to=synced_to, active_group=None
        ),
    )


async def test_group_colours_are_handed_out_for_the_visible_leaders() -> None:
    """Hand out the colour of every group whose leader the user can see."""
    everyone = [
        _grouped_player("kitchen", group_members=["kitchen", "dining"]),
        _grouped_player("dining", synced_to="kitchen"),
        # a group led by a player this user may not see still takes a colour
        _grouped_player("garage", group_members=["garage", "shed"]),
        _grouped_player("shed", synced_to="garage"),
        _grouped_player("office"),
    ]
    provider = _provider(["kitchen", "dining", "office"], [], [], all_players=everyone)

    result = await provider.get_player_locations()

    # the garage group took orange, so all users agree kitchen's group is purple
    assert result["group_colours"] == {"kitchen": "purple"}


async def test_group_colour_survives_a_change_of_leader() -> None:
    """Keep a group's colour when a member takes over from the leader between requests."""
    players = [
        _grouped_player("kitchen", group_members=["kitchen", "dining", "deck"]),
        _grouped_player("dining", synced_to="kitchen"),
        _grouped_player("deck", synced_to="kitchen"),
        _grouped_player("office", group_members=["office", "den"]),
        _grouped_player("den", synced_to="office"),
    ]
    provider = _provider(
        ["kitchen", "dining", "deck", "office", "den"], [], [], all_players=players
    )
    assert (await provider.get_player_locations())["group_colours"] == {
        "kitchen": "orange",
        "office": "purple",
    }

    # the kitchen leaves its group and the dining room takes over
    players[0:3] = [
        _grouped_player("kitchen"),
        _grouped_player("dining", group_members=["dining", "deck"]),
        _grouped_player("deck", synced_to="dining"),
    ]
    provider._on_player_event(MagicMock())

    assert (await provider.get_player_locations())["group_colours"] == {
        "dining": "orange",
        "office": "purple",
    }
