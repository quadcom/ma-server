"""Tests for picking a speaker's room (Home Assistant area) from Music Assistant."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hass_client.exceptions import FailedCommand
from music_assistant_models.enums import PlayerType
from music_assistant_models.errors import InvalidDataError, ProviderUnavailableError

from music_assistant.constants import CONF_GROUP_MEMBERS
from music_assistant.providers.hass import CONF_PLAYER_AREA, HomeAssistantProvider

AREAS = [
    {"area_id": "kitchen", "name": "Kitchen", "floor_id": "ground"},
    {"area_id": "bedroom", "name": "Bedroom", "floor_id": "upstairs"},
    {"area_id": "shed", "name": "Shed", "floor_id": None},
]
FLOORS = [
    {"floor_id": "ground", "name": "Ground floor", "level": 0},
    {"floor_id": "upstairs", "name": "Upstairs", "level": 1},
]


class _Cache:
    """Provide the slice of the cache controller the registry lookups rely on."""

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

    async def delete(self, key: str, **kwargs: Any) -> None:
        """Drop the data under the given key."""
        self.entries.pop(key, None)


def _device(device_id: str, player_id: str, area_id: str | None = None) -> dict[str, Any]:
    return {
        "id": device_id,
        "identifiers": [["music_assistant", player_id]],
        "area_id": area_id,
        "labels": [],
    }


def _entity(entity_id: str, device_id: str, area_id: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"ei": entity_id, "pl": "music_assistant", "di": device_id}
    if area_id:
        entry["ai"] = area_id
    return entry


def _player(player_id: str, player_type: PlayerType = PlayerType.PLAYER) -> Any:
    return SimpleNamespace(
        player_id=player_id,
        state=SimpleNamespace(type=player_type, group_members=[], static_group_members=[]),
    )


class _Setup:
    """A provider wired to a fake Home Assistant and fake Music Assistant settings."""

    def __init__(
        self,
        players: list[Any],
        devices: list[dict[str, Any]] | None = None,
        entities: list[dict[str, Any]] | None = None,
        settings: dict[tuple[str, str], Any] | None = None,
        *,
        connected: bool = True,
        fail: str | None = None,
    ) -> None:
        self.settings: dict[tuple[str, str], Any] = dict(settings or {})
        self.commands: list[tuple[str, dict[str, Any]]] = []
        entity_list = entities or []
        by_id = {player.player_id: player for player in players}

        async def _send_command(command: str, **kwargs: Any) -> Any:
            if command == "config/entity_registry/list_for_display":
                return {"entity_categories": {}, "entities": entity_list}
            if command == "config/floor_registry/list":
                return FLOORS
            self.commands.append((command, kwargs))
            if command == fail:
                msg = "The name Kitchen is already in use"
                raise FailedCommand(msg)
            if command == "config/floor_registry/create":
                return {"floor_id": "loft", "name": kwargs["name"]}
            if command == "config/area_registry/create":
                return {"area_id": "new_area", "name": kwargs["name"]}
            return None

        def _get_raw(player_id: str, key: str, default: Any = None) -> Any:
            return self.settings.get((player_id, key), default)

        def _set_raw(player_id: str, key: str, value: Any) -> None:
            self.settings[(player_id, key)] = value

        provider = HomeAssistantProvider.__new__(HomeAssistantProvider)
        provider.hass = SimpleNamespace(
            connected=connected,
            get_device_registry=AsyncMock(return_value=devices or []),
            get_area_registry=AsyncMock(return_value=AREAS),
            send_command=AsyncMock(side_effect=_send_command),
        )
        provider._entity_registry = None
        provider._entity_registry_lock = asyncio.Lock()
        provider.config = SimpleNamespace(  # type: ignore[assignment]
            instance_id="hass--test", get_value=lambda _key, default=None: default
        )
        provider.manifest = SimpleNamespace(domain="hass")  # type: ignore[assignment]
        mass_players = MagicMock()
        mass_players.get_player = MagicMock(side_effect=by_id.get)
        mass_players.all_players = MagicMock(return_value=players)
        mass_players.iter_players = MagicMock(side_effect=lambda: list(players))
        provider.mass = SimpleNamespace(  # type: ignore[assignment]
            cache=_Cache(),
            players=mass_players,
            config=SimpleNamespace(
                get_raw_player_config_value=_get_raw,
                set_raw_player_config_value=MagicMock(side_effect=_set_raw),
            ),
            call_later=MagicMock(),
            create_task=lambda coro, **_kwargs: asyncio.ensure_future(coro),
        )
        provider.logger = logging.getLogger("test.hass")
        provider._speaker_list_changed = MagicMock()  # type: ignore[method-assign]
        self.provider = provider

    def area_copy(self, player_id: str) -> Any:
        """Return the area Music Assistant keeps for a player."""
        return self.settings.get((player_id, CONF_PLAYER_AREA))

    def writes(self) -> list[tuple[str, dict[str, Any]]]:
        """Return the registry changes sent to Home Assistant."""
        return [command for command in self.commands if command[0].endswith(("update", "create"))]


async def test_areas_list_floors_areas_and_where_each_player_is() -> None:
    """Hand out Home Assistant's floors and areas, and each player's area from HA or the copy."""
    setup = _Setup(
        [_player("exposed"), _player("hidden"), _player("gone"), _player("nowhere")],
        [_device("dev1", "exposed", "kitchen")],
        [_entity("media_player.exposed", "dev1")],
        {("hidden", CONF_PLAYER_AREA): "bedroom", ("gone", CONF_PLAYER_AREA): "deleted"},
    )

    result = await setup.provider.get_areas()

    assert result == {
        "floors": [
            {"floor_id": "ground", "name": "Ground floor", "level": 0},
            {"floor_id": "upstairs", "name": "Upstairs", "level": 1},
        ],
        "areas": [
            {"area_id": "kitchen", "name": "Kitchen", "floor_id": "ground"},
            {"area_id": "bedroom", "name": "Bedroom", "floor_id": "upstairs"},
            {"area_id": "shed", "name": "Shed", "floor_id": None},
        ],
        "player_areas": {"exposed": "kitchen", "hidden": "bedroom", "gone": None, "nowhere": None},
    }


async def test_areas_need_home_assistant() -> None:
    """Raise when Home Assistant is not connected."""
    setup = _Setup([_player("a")], connected=False)

    with pytest.raises(ProviderUnavailableError):
        await setup.provider.get_areas()


async def test_set_area_of_an_exposed_speaker_goes_to_its_device() -> None:
    """Put the area on the speaker's HA device, clear the entity's override, and keep a copy."""
    setup = _Setup(
        [_player("speaker")],
        [_device("dev1", "speaker", "kitchen")],
        [_entity("media_player.speaker", "dev1", area_id="shed")],
    )

    await setup.provider.set_player_area("speaker", "bedroom")

    assert setup.writes() == [
        ("config/device_registry/update", {"device_id": "dev1", "area_id": "bedroom"}),
        ("config/entity_registry/update", {"entity_id": "media_player.speaker", "area_id": None}),
    ]
    assert setup.area_copy("speaker") == "bedroom"
    setup.provider._speaker_list_changed.assert_called()  # type: ignore[attr-defined]


async def test_set_area_of_a_speaker_not_exposed_is_kept_in_music_assistant() -> None:
    """Keep the area in Music Assistant only, for a speaker HA has no device for."""
    setup = _Setup([_player("speaker")])

    await setup.provider.set_player_area("speaker", "kitchen")

    assert setup.writes() == []
    assert setup.area_copy("speaker") == "kitchen"


async def test_set_no_area_clears_it_everywhere() -> None:
    """Take the speaker out of its area in HA, and keep that it is in none."""
    setup = _Setup(
        [_player("speaker")],
        [_device("dev1", "speaker", "kitchen")],
        [_entity("media_player.speaker", "dev1")],
        {("speaker", CONF_PLAYER_AREA): "kitchen"},
    )

    await setup.provider.set_player_area("speaker", None)

    assert setup.writes() == [
        ("config/device_registry/update", {"device_id": "dev1", "area_id": None})
    ]
    assert setup.area_copy("speaker") == ""


@pytest.mark.parametrize(("player_id", "area_id"), [("speaker", "deleted"), ("unknown", "kitchen")])
async def test_set_area_rejects_an_unknown_area_or_speaker(player_id: str, area_id: str) -> None:
    """Refuse an area HA doesn't have, or a player Music Assistant doesn't know."""
    setup = _Setup([_player("speaker")])

    with pytest.raises(InvalidDataError):
        await setup.provider.set_player_area(player_id, area_id)

    assert setup.writes() == []
    assert setup.area_copy("speaker") is None


async def test_create_area_on_an_existing_floor() -> None:
    """Make the area on the floor picked, with the name trimmed."""
    setup = _Setup([])

    result = await setup.provider.create_area(" Den ", floor_id="ground")

    assert result == {"area_id": "new_area", "floor_id": "ground"}
    assert setup.writes() == [
        ("config/area_registry/create", {"name": "Den", "floor_id": "ground"})
    ]


async def test_create_area_on_a_new_floor() -> None:
    """Make the new floor first, then the area on it."""
    setup = _Setup([])

    result = await setup.provider.create_area("Den", new_floor_name="Loft")

    assert result == {"area_id": "new_area", "floor_id": "loft"}
    assert setup.writes() == [
        ("config/floor_registry/create", {"name": "Loft"}),
        ("config/area_registry/create", {"name": "Den", "floor_id": "loft"}),
    ]


async def test_create_area_without_a_floor() -> None:
    """Make an area on no floor, without sending a floor to HA."""
    setup = _Setup([])

    await setup.provider.create_area("Den")

    assert setup.writes() == [("config/area_registry/create", {"name": "Den"})]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "  "},
        {"name": "Den", "floor_id": "ground", "new_floor_name": "Loft"},
        {"name": "Den", "floor_id": "deleted"},
        {"name": "Den", "new_floor_name": " "},
    ],
)
async def test_create_area_rejects_bad_input(kwargs: dict[str, Any]) -> None:
    """Refuse an empty name, two floors at once, an unknown floor or an empty floor name."""
    setup = _Setup([])

    with pytest.raises(InvalidDataError):
        await setup.provider.create_area(**kwargs)

    assert setup.writes() == []


async def test_create_area_passes_on_home_assistants_refusal() -> None:
    """Hand HA's reason on as a message an app can show, such as a name already in use."""
    setup = _Setup([], fail="config/area_registry/create")

    with pytest.raises(InvalidDataError, match="already in use"):
        await setup.provider.create_area("Kitchen")


async def test_sync_copy_follows_home_assistant() -> None:
    """Keep the area HA places an exposed speaker in, so it stays when no longer exposed."""
    setup = _Setup(
        [_player("speaker")],
        [_device("dev1", "speaker", "kitchen")],
        [_entity("media_player.speaker", "dev1")],
        {("speaker", CONF_PLAYER_AREA): "shed"},
    )

    await setup.provider._sync_area_copies()

    assert setup.area_copy("speaker") == "kitchen"
    assert setup.writes() == []


async def test_sync_new_device_takes_the_kept_area() -> None:
    """Put a speaker just exposed to HA (a device not seen before) in its kept area."""
    setup = _Setup(
        [_player("speaker")],
        [_device("dev1", "speaker")],
        [_entity("media_player.speaker", "dev1")],
        {("speaker", CONF_PLAYER_AREA): "bedroom"},
    )

    await setup.provider._sync_area_copies()

    assert setup.writes() == [
        ("config/device_registry/update", {"device_id": "dev1", "area_id": "bedroom"})
    ]


async def test_sync_area_removed_in_home_assistant_clears_the_copy() -> None:
    """Clear the kept area of a speaker that HA took out of its area after it was seen."""
    setup = _Setup(
        [_player("speaker")],
        [_device("dev1", "speaker")],
        [_entity("media_player.speaker", "dev1")],
        {("speaker", CONF_PLAYER_AREA): "bedroom"},
    )
    setup.provider._area_devices = frozenset({"dev1"})

    await setup.provider._sync_area_copies()

    assert setup.area_copy("speaker") == ""
    assert setup.writes() == []


async def test_sync_new_group_takes_the_area_its_rooms_share() -> None:
    """Put a new group (such as a stereo pair) in the area both of its speakers are in."""
    setup = _Setup(
        [_player("left"), _player("right"), _player("pair", PlayerType.GROUP)],
        [
            _device("dev_left", "left", "bedroom"),
            _device("dev_pair", "pair"),
        ],
        [_entity("media_player.left", "dev_left"), _entity("media_player.pair", "dev_pair")],
        {
            ("pair", CONF_GROUP_MEMBERS): ["left", "right"],
            # the right speaker is not exposed: its kept area counts
            ("right", CONF_PLAYER_AREA): "bedroom",
        },
    )
    # the pair's device was seen before, still without an area
    setup.provider._area_devices = frozenset({"dev_pair"})

    await setup.provider._sync_area_copies()

    assert setup.area_copy("pair") == "bedroom"
    assert ("config/device_registry/update", {"device_id": "dev_pair", "area_id": "bedroom"}) in (
        setup.writes()
    )


@pytest.mark.parametrize(
    ("right_area", "pair_copy"),
    [
        # the speakers are in different rooms
        ("kitchen", None),
        # the group was deliberately put in no room
        ("bedroom", ""),
    ],
)
async def test_sync_group_without_a_shared_or_with_a_chosen_area_is_left(
    right_area: str, pair_copy: str | None
) -> None:
    """Leave a group whose rooms are spread out, or one put in no room on purpose."""
    settings: dict[tuple[str, str], Any] = {
        ("pair", CONF_GROUP_MEMBERS): ["left", "right"],
        ("left", CONF_PLAYER_AREA): "bedroom",
        ("right", CONF_PLAYER_AREA): right_area,
    }
    if pair_copy is not None:
        settings[("pair", CONF_PLAYER_AREA)] = pair_copy
    setup = _Setup(
        [_player("left"), _player("right"), _player("pair", PlayerType.GROUP)], settings=settings
    )

    await setup.provider._sync_area_copies()

    assert setup.area_copy("pair") == pair_copy
    assert setup.writes() == []


async def test_sync_does_nothing_while_disconnected() -> None:
    """Leave everything as it is while Home Assistant is not connected."""
    setup = _Setup([_player("speaker")], connected=False)

    await setup.provider._sync_area_copies()

    assert setup.commands == []
