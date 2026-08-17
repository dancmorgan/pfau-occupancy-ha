# Planet Fitness AU Occupancy (Home Assistant)

A Home Assistant custom integration that exposes several sensors helpful for those who attend a Planet Fitness gym. These include:
 - Reported Occupancy Sensor - What Planet Fitness publishes from its own in-club check in sensors.
 - Real Occupancy Sensor - A more accurate number compared to Reported Occupancy (see below)
 - Busyness Sensor - Uses the clubs square-meterage to determine how crowded the club feels compared to just a single occupancy number.
 - Staffing Status Sensor - Informs you if staff are present within the club or not. This is necessary for black card holders who want to know when they will be able to access the spa.
 - Next Staff Status Change Sensor - A timestamp of when staff next arrive or leave, so you can trigger an automation or alarm directly off it.
 - Occupancy Trend Sensor - Whether the club is currently filling up or emptying out.

<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/integrationsplash.png" width="900">

<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/clubdetail.png" width="900">

Authentication with and access to the occupancy API is backed by [pfau-occupancy](https://github.com/dancmorgan/pfau-occupancy), the async client for the Planet Fitness AU member portal (PerfectGym ClientPortal2). This repo contains only the Home Assistant side: config flow, coordinator, and entities.

## Install via HACS

Add this repository as a custom repository in HACS (URL: https://github.com/dancmorgan/pfau-occupancy-ha, category: Integration), then install "Planet Fitness AU Occupancy" and restart Home Assistant.

## Configure

Settings → Devices & Services → Add Integration → "Planet Fitness AU Occupancy". Enter your Planet Fitness Australia member portal email and password.

The integration will ask you for names and locations for all clubs (represented as devices) - this step can simply be skipped without issue.

Once set up, the integration's Options (a menu, reached the same way) let you tune the polling interval (default 5 minutes), the occupancy reduction percentage, the busy/crowded thresholds - either generally or overridden for one specific club - and the Occupancy Trend window (default 45 minutes) - all described below. Floor area and hours are not configurable there.

<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/integrationoptions.png" width="400">

<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/overrideclubselect.png" width="400">

<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/overrideclubnumbers.png" width="400">

# What you get

Each discovered club is its own Device (Settings → Devices & Services → Planet Fitness AU Occupancy), grouping its nine entities under one card instead of a flat entity list. Since a device is created per club, first setup (or any time a new club appears) prompts Home Assistant's usual "N new devices found" review - worth it for the grouping.

Per club:
## Sensors
| Entity | Type | Notes |
| --- | --- | --- |
| Reported Occupancy | number | the number the Planet Fitness portal shows which is based on member checkins. Planet Fitness does not have any check out mechanism so PF relies on a static two hour timer to decrement the occupancy counter. This creates situations where members leave the gym after 45 mins but are considered still within the gym for another 1 hour 15 mins. |
| Real Occupancy | number | corrects this based on real counts and observations over time (albeit at a specific gym. Yours may differ and so the reduction % is provided as a configurable option). The results of the real occupancy counts have identified that the occupancy counter is on average 210% overstating gym occupancy and so the default reduction of the real occupancy sensor has been set to 50%. Its worth noting that given the way occupancy is reported via the API, theres very little ability to do authentic time tracking or analysis or heuristic based results. |
| Busyness | category | human friendly category displaying "how the gym feels" (eg. Quiet, Busy, and Crowded) based on density configurations measured against 36m2 square (6x6 meters around you). Defaults are Quiet = less than 1.8 people, Busy more than 1.8 people, Crowded more than 2.5 people. |
| Staffing Status | boolean | provides an indication as to whether staff are in the gym or not. This is specifically important if you have a query or if you are a black card member who wishes to use the spa. |
| Next Staff Status Change | timestamp | describes the timestamp when staff will either arrive or leave. Set an automation 30 minutes before it to catch the last of the black card spa amenities before staff leave (useful on weekends, when they tend to leave earlier). |
| Occupancy Trend | boolean | reads "Getting Busier" or "Getting Quieter" depending on which way Real Occupancy is heading. |
## Diagnostic
| Entity | Type | Notes |
| --- | --- | --- |
| Floor Area | number | A simple passthrough value that shows you the club's square meter area as reported by the Planet Fitness website. It is raw and not altered (busyness dead-space subtraction is not applied.) |
| Busy Threshold | number | A simple calculated number based on specified thresholds on when the gym is considered busy (either based on default values or your own) |
| Crowded Threshold | number | A simple calculated number based on specified thresholds on when the gym is considered crowded (either based on default values or your own) |

Clubs are discovered automatically on each poll; a club that disappears from a response (e.g. renamed) goes `unavailable` rather than being deleted, since the API exposes no stable club ID other than the (slugified) name.

## Busyness, Density, and Trend

Busyness provides a more useful metric than Real or Reported Occupancy as it divides **Real Occupancy** by `area_sqm` and bands the result to give you a more useful "does the gym feel busy right now" category. Density rather than occupancy is what makes clubs comparable - 60 people in a 1,600 m²
warehouse is quiet, the same 60 in a 400 m² studio is extremely busy.

Before dividing, 33% of `area_sqm` is subtracted as dead space - walkways, corridors, and the footprint of the racks and machines themselves, none of which is room a member can actually stand in. 

36 square metres - a 6x6 metre square - has been chosen as the "people per" metric to calculate density as it allows a user to mentally picture that box around them during a workout and decide their own threshold for how many people within it they consider too much.

Since these categories are subjective, they're fully user-configurable: set them generally in the integration's Options, or override just one club (Options → "Override a club's threshold") if it consistently feels busier or quieter than the general setting suggests. Leave both fields blank on that per-club form to remove the override again.

Trend indicates if the gym is actively filling or emptying so you know whether or not its going to be better or worse by the time you get there. There is some smoothing and deadening to ensure it does not flap. Works off any change greater than 2 of the Real Occupancy sensor over a period of 45 minutes (window and deadening configurable in settings). Defaults to "Getting Busier" on reboot or state loss.

## Club List

The portal's occupancy endpoint returns a club's name, address, current count and capacity limit - and nothing else. Opening hours and floor size aren't in it, or anywhere else in the API, so they come from [`clubs.yaml`](custom_components/pfau_occupancy/clubs.yaml) - pre-seeded with every AU club at the time of publishing. Until a club has an entry there, its Staffing, Floor Area, Busyness and threshold sensors report `unavailable`; the two occupancy sensors don't depend on it and work regardless.

If a new club opens or an existing club closes, please raise an issue or pull request for inclusion.

`clubs.yaml` is **refetched from this repo** by the running integration on restart and every 24 hours, so a correction or a newly-added club reaches everyone without waiting for the next release. If GitHub is unreachable when the integration starts, it falls back to the last successfully fetched copy (cached in your Home Assistant config directory), and if there's no cache yet either, to whatever version shipped with your installed release.

## Disclaimer

Uses the member portal's internal, undocumented endpoints with your own
membership credentials. Keep polling gentle; this may break if Planet Fitness
changes the portal. Not affiliated with or endorsed by Planet Fitness.