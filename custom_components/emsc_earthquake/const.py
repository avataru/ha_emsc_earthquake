"""Constants for the EMSC Earthquake integration."""

from datetime import timedelta

DOMAIN = "emsc_earthquake"
DEFAULT_NAME = "Latest Earthquake"

CONF_CENTER_LATITUDE = "center_latitude"
CONF_CENTER_LONGITUDE = "center_longitude"
CONF_RADIUS_KM = "radius_km"
CONF_MIN_MAG = "min_mag"
CONF_TOTAL_MAX_MAG = "total_max_mag"

WEBSOCKET_URL = "wss://www.seismicportal.eu/standing_order/websocket"
PING_INTERVAL = 15  # Seconds between websocket keep-alive pings.
RECONNECT_DELAY = 10  # Seconds to wait before reconnecting after a disconnect.

# EMSC also sends "update" messages for events that happened weeks or months
# ago. Events older than this are ignored unless they update the event that
# is currently being shown.
MAX_EVENT_AGE = timedelta(hours=24)
