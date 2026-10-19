"""Sensor for near-real-time earthquake reports from the EMSC Seismic Portal."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from math import atan2, cos, radians, sin, sqrt
import ssl
from typing import Any

import websockets

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CENTER_LATITUDE,
    CONF_CENTER_LONGITUDE,
    CONF_MIN_MAG,
    CONF_RADIUS_KM,
    CONF_TOTAL_MAX_MAG,
    DEFAULT_NAME,
    MAX_EVENT_AGE,
    PING_INTERVAL,
    RECONNECT_DELAY,
    WEBSOCKET_URL,
)

_LOGGER = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0
HANDLED_ACTIONS = ("create", "update")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EMSC Earthquake sensor from a config entry."""
    # Values saved in the Configure dialog (options) take precedence over the
    # values entered during the initial setup (data).
    config = {**entry.data, **entry.options}
    async_add_entities(
        [
            EMSCEarthquakeSensor(
                name=config.get(CONF_NAME, DEFAULT_NAME),
                center_latitude=float(config[CONF_CENTER_LATITUDE]),
                center_longitude=float(config[CONF_CENTER_LONGITUDE]),
                radius_km=float(config[CONF_RADIUS_KM]),
                min_mag=float(config[CONF_MIN_MAG]),
                total_max_mag=float(config[CONF_TOTAL_MAX_MAG]),
            )
        ]
    )


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle (haversine) distance between two points in km."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return EARTH_RADIUS_KM * 2 * atan2(sqrt(a), sqrt(1 - a))


def _parse_time(value: Any) -> datetime | None:
    """Parse an EMSC timestamp into a timezone-aware datetime."""
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _is_number(value: Any) -> bool:
    """Return True for ints and floats (but not booleans)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class EMSCEarthquakeSensor(SensorEntity, RestoreEntity):
    """The most recent earthquake that matches the configured filters."""

    _attr_icon = "mdi:waveform"
    _attr_should_poll = False

    def __init__(
        self,
        name: str,
        center_latitude: float,
        center_longitude: float,
        radius_km: float,
        min_mag: float,
        total_max_mag: float,
    ) -> None:
        """Initialise the sensor."""
        self._attr_name = name
        # Kept identical to earlier versions so existing entities keep their
        # entity ID, history and dashboard references after updating.
        self._attr_unique_id = f"emsc_earthquake_{name}"
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._center_latitude = center_latitude
        self._center_longitude = center_longitude
        self._radius_km = radius_km
        self._min_mag = min_mag
        self._total_max_mag = total_max_mag
        self._event_time: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last event and start listening to the EMSC feed."""
        await super().async_added_to_hass()
        await self._async_restore_last_event()

        # Creating an SSL context loads certificates from disk, which blocks,
        # so it is done in the executor rather than the event loop.
        ssl_context = await self.hass.async_add_executor_job(
            ssl.create_default_context
        )
        task = self.hass.async_create_background_task(
            self._async_listen(ssl_context),
            name=f"EMSC earthquake websocket for {self.entity_id}",
        )
        # Cancel the websocket when the entity is removed (for example when
        # the integration is reloaded or unloaded), so no connection is left
        # running in the background with outdated settings.
        self.async_on_remove(task.cancel)

    def _passes_filters(self, magnitude: float, distance: float) -> bool:
        """Return True if the event should be shown according to the settings."""
        if magnitude >= self._total_max_mag:
            return True
        return distance <= self._radius_km and magnitude >= self._min_mag

    async def _async_restore_last_event(self) -> None:
        """Restore the last earthquake shown before Home Assistant restarted."""
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        attrs = last_state.attributes
        lat, lon, mag = attrs.get("lat"), attrs.get("lon"), attrs.get("magnitude")
        if not (_is_number(lat) and _is_number(lon) and _is_number(mag)):
            return

        distance = _distance_km(self._center_latitude, self._center_longitude, lat, lon)
        if not self._passes_filters(mag, distance):
            _LOGGER.debug(
                "Not restoring event %s because it no longer matches the filters",
                attrs.get("unid"),
            )
            return

        properties = {
            "unid": attrs.get("unid"),
            "time": attrs.get("time"),
            "mag": mag,
            "flynn_region": attrs.get("region"),
            "depth": attrs.get("depth"),
            "lat": lat,
            "lon": lon,
            "magtype": attrs.get("magtype"),
        }
        # Home Assistant writes the state itself after async_added_to_hass.
        self._apply_event(attrs.get("action"), properties, distance, write_state=False)

    async def _async_listen(self, ssl_context: ssl.SSLContext) -> None:
        """Keep a websocket connection to EMSC open and process its messages."""
        while True:
            try:
                _LOGGER.debug("Connecting to %s", WEBSOCKET_URL)
                async with websockets.connect(
                    WEBSOCKET_URL, ssl=ssl_context, ping_interval=PING_INTERVAL
                ) as websocket:
                    _LOGGER.info("Connected to the EMSC websocket")
                    async for message in websocket:
                        try:
                            self._handle_message(message)
                        except Exception:  # noqa: BLE001
                            # One malformed message must not drop the connection.
                            _LOGGER.exception("Error processing EMSC message")
                _LOGGER.warning("The EMSC websocket was closed by the server")
            except (OSError, websockets.WebSocketException) as err:
                _LOGGER.warning("EMSC websocket error: %s", err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in the EMSC websocket listener")
            # asyncio.CancelledError is not caught above, so cancelling the
            # task on unload ends this loop immediately.
            await asyncio.sleep(RECONNECT_DELAY)

    def _handle_message(self, raw: str | bytes) -> None:
        """Decide whether an EMSC message should update the sensor."""
        try:
            message = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Ignoring message that is not valid JSON: %.200s", raw)
            return

        action = message.get("action")
        properties = (message.get("data") or {}).get("properties") or {}
        unid = properties.get("unid")
        lat, lon, mag = properties.get("lat"), properties.get("lon"), properties.get("mag")

        if action not in HANDLED_ACTIONS:
            _LOGGER.debug("Ignoring message with action %s for %s", action, unid)
            return
        if not (_is_number(lat) and _is_number(lon) and _is_number(mag)):
            _LOGGER.debug("Ignoring %s: missing location or magnitude", unid)
            return
        event_time = _parse_time(properties.get("time"))
        if event_time is None:
            _LOGGER.debug("Ignoring %s: missing or invalid time", unid)
            return

        distance = _distance_km(self._center_latitude, self._center_longitude, lat, lon)
        is_current_event = (
            unid is not None
            and unid == self._attr_extra_state_attributes.get("unid")
        )

        # Updates to the event currently shown are always applied, so revised
        # magnitudes and locations come through. Anything else must be recent,
        # not older than the event currently shown, and match the filters.
        if not is_current_event:
            if dt_util.utcnow() - event_time > MAX_EVENT_AGE:
                _LOGGER.debug("Ignoring %s: event from %s is too old", unid, event_time)
                return
            if self._event_time is not None and event_time < self._event_time:
                _LOGGER.debug("Ignoring %s: older than the event shown", unid)
                return
            if not self._passes_filters(mag, distance):
                _LOGGER.debug(
                    "Ignoring %s: M%s at %.0f km does not match the filters",
                    unid,
                    mag,
                    distance,
                )
                return

        self._apply_event(action, properties, distance)

    def _apply_event(
        self,
        action: str | None,
        properties: dict[str, Any],
        distance: float,
        write_state: bool = True,
    ) -> None:
        """Store an event as the sensor's state and attributes."""
        lat, lon, mag = properties["lat"], properties["lon"], properties["mag"]
        self._event_time = _parse_time(properties.get("time"))
        self._attr_native_value = mag
        self._attr_extra_state_attributes = {
            "action": action,
            "unid": properties.get("unid"),
            "time": properties.get("time"),
            "magnitude": mag,
            "region": properties.get("flynn_region"),
            "depth": properties.get("depth"),
            # "lat" and "lon" are kept for existing cards and automations;
            # "latitude" and "longitude" let the map card show the event.
            "lat": lat,
            "lon": lon,
            ATTR_LATITUDE: lat,
            ATTR_LONGITUDE: lon,
            "distance_km": round(distance, 1),
            "magtype": properties.get("magtype"),
        }
        _LOGGER.debug("Showing earthquake: %s", self._attr_extra_state_attributes)
        if write_state:
            self.async_write_ha_state()
