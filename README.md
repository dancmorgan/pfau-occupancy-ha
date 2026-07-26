# Planet Fitness AU Occupancy (Home Assistant)

A Home Assistant custom integration that reports live club occupancy for
Planet Fitness Australia clubs: the count the portal reports, an estimate of
how many people are actually there based on real experience, and — for clubs
you've described in `clubs.yaml` — whether the club is currently staffed and
how crowded that estimate makes it feel.

Backed by [pfau-occupancy](https://github.com/dancmorgan/pfau-occupancy), the
async client for the Planet Fitness AU member portal (PerfectGym
ClientPortal2). This repo contains only the Home Assistant side: config flow,
coordinator, and entities.

## Install via HACS

Add this repository as a custom repository in HACS (category: Integration),
then install "Planet Fitness AU Occupancy" and restart Home Assistant.

## Configure

Settings → Devices & Services → Add Integration → "Planet Fitness AU
Occupancy". Enter your Planet Fitness Australia member portal email and
password.

Once set up, the integration's Options let you tune the polling interval
(default 5 minutes), the occupancy reduction percentage, and the busy /
crowded thresholds — all described below.

## Reported vs Real occupancy

Each club gets two sensors.

**Reported Occupancy** is the number the Planet Fitness portal shows which is based on member checkins. Planet Fitness does not have any check out mechanism so PF relies on a static two hour timer to decrement the occupancy counter. This creates situations where members leave the gym after 45 mins but are considered still within the gym for another 1 hour 15 mins.

**Real Occupancy** corrects this based on real counts and observations over time (albeit at a specific gym. Yours may differ and so the reduction % is provided as a configurable option). The results of the real occupancy counts have identified that the occupancy counter is on average 210% overstating gym occupancy and so the default reduction of the real occupancy sensor has been set to 50%. Its worth noting that given the way occupancy is reported via the API, theres very little ability to do authentic time tracking or analysis or heuristic based results.

## Staffed hours and floor area

The portal's occupancy endpoint returns a club's name, address, current count
and capacity limit — and nothing else. Opening hours and floor size aren't in
it, or anywhere else in the API we've found, so they live in
[`custom_components/pfau_occupancy/clubs.yaml`](custom_components/pfau_occupancy/clubs.yaml)
and are maintained by hand. **It ships empty**: until you add a club, that
club's Staffing, Floor Area and Busyness sensors report `unavailable`. The
two occupancy sensors don't depend on it and work regardless.

```yaml
clubs:
  morayfield:
    name: Morayfield
    area_sqm: 1200
    timezone: Australia/Brisbane   # only needed outside HA's own timezone
    # No `open:` key — this club is open 24/7.
    staffed:
      weekdays:
        - 09:00-13:00
        - 15:00-19:00
      sat: 09:00-13:00
```

Keys are the club's slugified name, the same slug already in your entity IDs —
`sensor.morayfield_reported_occupancy` means the key is `morayfield`. Turn on
debug logging for `custom_components.pfau_occupancy` and it lists every
discovered club that has no entry yet.

Day keys are `mon`…`sun` plus `daily`, `weekdays` and `weekends`. Times are
`HH:MM-HH:MM`; `24:00` means end-of-day, and a range that ends at or before it
starts (`22:00-02:00`) runs past midnight. Omitting `open` means open 24/7,
omitting `staffed` means never staffed. The full field reference is in the
comments at the top of the file.

For `area_sqm`, use the floor members actually train on — weights, cardio and
studios — and leave out offices, change rooms and parking, or crowding will
read low. Note that the file is part of the integration, so a HACS update
replaces it; keep a copy of your entries.

## Quiet, busy, crowded

The Busyness sensor divides **Real Occupancy** by `area_sqm` and bands the
result. Density is what makes clubs comparable — 60 people in a 1,600 m²
warehouse is a quiet morning, the same 60 in a 400 m² studio is a queue for
the squat rack.

Before dividing, 33% of `area_sqm` is subtracted as dead space — walkways,
corridors, and the footprint of the racks and machines themselves, none of
which is room a member can actually stand in. Busyness is measured against
that smaller effective area, which tracks how packed the floor feels more
closely than the raw square metreage does.

Thresholds are in people per 16 square metres of that effective area, and are
easiest to read as their reciprocal: the default `busy` of 0.8 is one person
per 20 m², and `crowded` of 1.6 is one per 10 m². Both are configurable in the
integration's Options, and any club can override them with its own `busy:` /
`crowded:` keys in `clubs.yaml`.

## What you get

Per club:

| Entity | State | Notes |
| --- | --- | --- |
| `sensor.<club>_reported_occupancy` | count | Attributes: address, limit, percent full |
| `sensor.<club>_real_occupancy` | count | Attributes: raw count, reduction % |
| `sensor.<club>_staffing` | `staffed` / `unstaffed` / `closed` | Attributes: next change, next state, today's and tomorrow's staffed windows |
| `sensor.<club>_floor_area` | m² | Diagnostic |
| `sensor.<club>_busyness` | `quiet` / `busy` / `crowded` | Attributes: people/16m², m²/person, thresholds in force |

Staffing follows the clock rather than the poll: it schedules its own update
for the next boundary in that club's week, so it flips at 9am sharp instead of
whenever the next poll lands.

Clubs are discovered automatically on each poll; a club that disappears from a
response (e.g. renamed) goes `unavailable` rather than being deleted, since the
API exposes no stable club ID other than the (slugified) name.

## Tests

The estimator, hours, density and club-data modules have no Home Assistant
imports, so they run under plain pytest:

```bash
pip install -r tests/requirements.txt
pytest tests/
```

## Disclaimer

Uses the member portal's internal, undocumented endpoints with your own
membership credentials. Keep polling gentle; this may break if Planet Fitness
changes the portal. Not affiliated with or endorsed by Planet Fitness.