# Planet Fitness AU Occupancy (Home Assistant)

A Home Assistant custom integration that reports live club occupancy for
Planet Fitness Australia clubs — two sensors per club: the count the portal
reports, and an estimate of how many people are actually there.

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
(default 5 minutes) and the occupancy reduction percentage described below.

## Reported vs Real occupancy

Each club gets two sensors, and the difference between them matters.

**Reported Occupancy** is the number the Planet Fitness portal shows. But
here's the catch: it isn't a headcount. Each time a member scans in, the
counter goes up by one — and then goes back down **on a fixed 2-hour timer**,
regardless of when that member actually walks out. (The 2-hour behaviour was
confirmed to me directly by a Planet Fitness staff member, so this isn't
guesswork about how the counter works.)

Most people don't spend 2 hours at the gym — a typical visit is closer to an
hour. That means the reported number effectively counts everyone who arrived
in the last 2 hours, including plenty of people who have already left. At busy
times it can roughly **double** the real crowd.

**Real Occupancy** corrects for this. Because the portal's own removal timer
is baked into the same signal as new check-ins, there's no reliable way to
work backwards from the reported number and separate genuine new arrivals from
the portal's own decay — any attempt to reconstruct arrivals that way gets
poisoned by check-in noise. So instead, the Real Occupancy sensor applies a
flat percentage reduction to the reported count (default 33%, i.e. `real =
reported × 0.67`).

Why it's important: if you're using these sensors to decide when to go, the
reported number will tell you the gym is busier than it really is. The Real
Occupancy sensor is the one that gives a more realistic read on how many
people are in there right now.

A few practical notes:

- The reduction percentage is adjustable in the integration's Options if your
  club behaves differently.
- It's a flat correction, not a turnstile count — it doesn't know arrival or
  departure times, just a rough average.

## What you get

Two sensors per club — `sensor.<club_name>_reported_occupancy` and
`sensor.<club_name>_real_occupancy` — with address, capacity limit, and
model details as attributes. Clubs are discovered automatically on each poll;
a club that disappears from a response (e.g. renamed) goes `unavailable`
rather than being deleted, since the API exposes no stable club ID other than
the (slugified) name.

## Disclaimer

Uses the member portal's internal, undocumented endpoints with your own
membership credentials. Keep polling gentle; this may break if Planet Fitness
changes the portal. Not affiliated with or endorsed by Planet Fitness.
