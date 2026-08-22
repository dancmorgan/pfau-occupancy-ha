"""Constants for the Planet Fitness AU Occupancy integration."""

DOMAIN = "pfau_occupancy"

DEFAULT_SCAN_INTERVAL_MINUTES = 5

# The portal's reported count overstates real occupancy: its own fixed removal timer keeps a member in the count long after they've likely left, and that removal is entangled with new arrivals in the same signal, so it can't be reliably inverted. Applied as a flat percentage reduction on the reported count.
CONF_REDUCTION_PERCENT = "reduction_percent"
DEFAULT_REDUCTION_PERCENT = 50

# Crowding thresholds in people per 36 square metres (a 6m by 6m square) of usable gym floor, applied to the estimated real occupancy. This is a subjective, personal-feel setting rather than a fact about a club, so unlike area_sqm/hours it's user-configurable — globally here, and per club via CONF_CLUB_THRESHOLDS below.
CONF_BUSY_THRESHOLD = "busy_threshold"
CONF_CROWDED_THRESHOLD = "crowded_threshold"
DEFAULT_BUSY_THRESHOLD = 1.5
DEFAULT_CROWDED_THRESHOLD = 2.0

# Occupancy Trend fits a line through the real-occupancy samples collected over a rolling window and reports its gradient's direction. A short window reacts quickly but is noisier; a long one is steadier but lags. Subjective enough to be worth exposing, so it's in the options flow. Whatever the user picks is still widened when polling is slower than it, so the fit always has a few samples to work with (see coordinator.trend_window_seconds).
CONF_TREND_WINDOW_MINUTES = "trend_window_minutes"
DEFAULT_TREND_WINDOW_MINUTES = 60
TREND_MIN_SAMPLES_IN_WINDOW = 4
# Deadband half-width in people per hour. A club drifting more slowly than this in either direction isn't really moving, so the trend holds whatever it was last reporting rather than flip-flopping around zero.
TREND_MIN_GRADIENT = 10.0

# Per-club busy/crowded overrides, set through the options flow GUI rather than any file. Stored as entry.options[CONF_CLUB_THRESHOLDS] = {club_key: {CONF_BUSY_THRESHOLD: float, CONF_CROWDED_THRESHOLD: float}}; a club with no entry here just uses the global thresholds above. See coordinator.thresholds() and config_flow.py's club_threshold_values step.
CONF_CLUB_THRESHOLDS = "club_thresholds"

# clubs.yaml is refetched from the repo (rather than only shipping with a release) so club data can be updated without every user pulling a new version. A network hiccup falls back to the last successful fetch cached on disk, then to the copy bundled with the integration; see coordinator.py. This data is authored by the maintainer only — there is no per-user override for it, by design (see club_data.py).
CLUB_DATA_URL = (
    "https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/"
    "main/custom_components/pfau_occupancy/clubs.yaml"
)
CLUB_DATA_REFRESH_HOURS = 24
CLUB_DATA_CACHE_FILENAME = "pfau_occupancy_clubs_cache.yaml"
