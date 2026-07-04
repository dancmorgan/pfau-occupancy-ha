# Planet Fitness AU Occupancy (Home Assistant)

A Home Assistant custom integration that reports live club occupancy for
Planet Fitness Australia clubs, one sensor per club.

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

Once set up, the polling interval (default 5 minutes) can be tuned from the
integration's Options.

## What you get

One sensor per club, named `sensor.<club_name>_occupancy`, with the current
member count as its state and address/limit/percent-full as attributes. Clubs
are discovered automatically on each poll; a club that disappears from a
response (e.g. renamed) goes `unavailable` rather than being deleted, since the
API exposes no stable club ID other than the (slugified) name.

## Disclaimer

Uses the member portal's internal, undocumented endpoints with your own
membership credentials. Keep polling gentle; this may break if Planet Fitness
changes the portal.
