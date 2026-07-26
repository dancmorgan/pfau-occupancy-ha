"""Constants for the Planet Fitness AU Occupancy integration."""

DOMAIN = "pfau_occupancy"

DEFAULT_SCAN_INTERVAL_MINUTES = 5

# The portal's reported count overstates real occupancy: its own fixed
# removal timer keeps a member in the count long after they've likely left,
# and that removal is entangled with new arrivals in the same signal, so it
# can't be reliably inverted. Applied as a flat percentage reduction on the
# reported count.
CONF_REDUCTION_PERCENT = "reduction_percent"
DEFAULT_REDUCTION_PERCENT = 50

# Crowding thresholds in people per 16 square metres of usable gym floor,
# applied to the estimated real occupancy. Easier to reason about as their
# reciprocal: 0.8 is one person per 20 m2 (roomy), 1.6 is one per 10 m2
# (shoulder to shoulder around the racks). Individual clubs can override both
# in clubs.yaml.
CONF_BUSY_THRESHOLD = "busy_threshold"
CONF_CROWDED_THRESHOLD = "crowded_threshold"
DEFAULT_BUSY_THRESHOLD = 0.8
DEFAULT_CROWDED_THRESHOLD = 1.6
