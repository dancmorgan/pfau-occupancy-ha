"""Constants for the Planet Fitness AU Occupancy integration."""

DOMAIN = "pfau_occupancy"

DEFAULT_SCAN_INTERVAL_MINUTES = 5

# The portal's reported count overstates real occupancy: its own fixed
# removal timer keeps a member in the count long after they've likely left,
# and that removal is entangled with new arrivals in the same signal, so it
# can't be reliably inverted. Applied as a flat percentage reduction on the
# reported count.
CONF_REDUCTION_PERCENT = "reduction_percent"
DEFAULT_REDUCTION_PERCENT = 33
