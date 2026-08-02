"""Constants for the Planet Fitness AU Occupancy integration."""

DOMAIN = "pfau_occupancy"

DEFAULT_SCAN_INTERVAL_MINUTES = 5

# The portal's reported count overstates real occupancy: its own fixed removal timer keeps a member in the count long after they've likely left, and that removal is entangled with new arrivals in the same signal, so it can't be reliably inverted. Applied as a flat percentage reduction on the reported count.
CONF_REDUCTION_PERCENT = "reduction_percent"
DEFAULT_REDUCTION_PERCENT = 50

# Crowding thresholds in people per 16 square metres of usable gym floor, applied to the estimated real occupancy. Easier to reason about as their reciprocal: 0.8 is one person per 20 m2 (roomy), 1.6 is one per 10 m2 (shoulder to shoulder around the racks). This is a subjective, personal-feel setting rather than a fact about a club, so unlike area_sqm/hours it's user-configurable — globally here, and per club via CONF_CLUB_THRESHOLDS below.
CONF_BUSY_THRESHOLD = "busy_threshold"
CONF_CROWDED_THRESHOLD = "crowded_threshold"
DEFAULT_BUSY_THRESHOLD = 0.5
DEFAULT_CROWDED_THRESHOLD = 1.2

# Per-club busy/crowded overrides, set through the options flow GUI rather than any file. Stored as entry.options[CONF_CLUB_THRESHOLDS] = {club_key: {CONF_BUSY_THRESHOLD: float, CONF_CROWDED_THRESHOLD: float}}; a club with no entry here just uses the global thresholds above. See coordinator.thresholds() and config_flow.py's club_threshold_values step.
CONF_CLUB_THRESHOLDS = "club_thresholds"

# clubs.yaml is refetched from the repo (rather than only shipping with a release) so club data can be updated without every user pulling a new version. A network hiccup falls back to the last successful fetch cached on disk, then to the copy bundled with the integration; see coordinator.py. This data is authored by the maintainer only — there is no per-user override for it, by design (see club_data.py).
CLUB_DATA_URL = (
    "https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/"
    "main/custom_components/pfau_occupancy/clubs.yaml"
)
CLUB_DATA_REFRESH_HOURS = 24
CLUB_DATA_CACHE_FILENAME = "pfau_occupancy_clubs_cache.yaml"
