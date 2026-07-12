"""Constants for the Planet Fitness AU Occupancy integration."""

DOMAIN = "pfau_occupancy"

DEFAULT_SCAN_INTERVAL_MINUTES = 5

# The portal's fixed removal timer: a member scan adds 1 to the raw counter
# and is removed this many minutes later regardless of actual departure.
CONF_COUNTER_WINDOW = "counter_window_minutes"
DEFAULT_COUNTER_WINDOW_MINUTES = 120

# Assumed true average dwell used to re-sum the reconstructed arrival flow.
CONF_REAL_DWELL = "real_dwell_minutes"
DEFAULT_REAL_DWELL_MINUTES = 60
