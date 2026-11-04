"""Tests for looking up where Music Assistant players are placed in Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from music_assistant_models.errors import ProviderUnavailableError

from music_assistant.providers.hass import HomeAssistantProvider

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
    provider.config = SimpleNamespace(instance_id="hass--test")  # type: ignore[assignment]
    provider.manifest = SimpleNamespace(domain="hass")  # type: ignore[assignment]
    players = MagicMock()
    players.all_players = MagicMock(
        return_value=[SimpleNamespace(player_id=player_id) for player_id in player_ids]
    )
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
        "players": {
            "player1": {
                "area": "Kitchen",
                "floor": "Ground floor",
                "floor_level": 0,
                "labels": [],
            }
        }
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
