<div align="center">

<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/custom_components/pfau_occupancy/brand/icon.png" width="128" alt="Planet Fitness AU Occupancy">

# Planet Fitness AU Occupancy

Live occupancy, crowding, staffing and trend sensors for every Planet Fitness Australia club.

<br>

[![Release][release-badge]][release-url]
[![Validate][validate-badge]][validate-url]
[![HACS][hacs-badge]][hacs-url]
[![Home Assistant][ha-badge]][ha-url]
[![License][license-badge]][license-url]

<br>

---

<table>
<tr>
<td>
<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/integrationsplash.png" width="860" alt="All clubs listed as devices in Home Assistant">
</td>
<td>
<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/clubdetail.png" width="860" alt="A single club's device page">
</td>
</tr>
</table>


</div>

<div align="left">

## Sensors

Nine entities per club, grouped as one device.

| | Entity | Answers |
| :-: | --- | --- |
| 👥 | **Reported Occupancy** | the number the Planet Fitness portal shows which is based on member checkins. Planet Fitness does not have any check out mechanism so PF relies on a static two hour timer to decrement the occupancy counter. This creates situations where members leave the gym after 45 mins but are considered still within the gym for another 1 hour 15 mins. |
| 👥 | **Real Occupancy** | corrects this based on real counts and observations over time (albeit at a specific gym. Yours may differ and so the reduction % is provided as a configurable option). The results of the real occupancy counts have identified that the occupancy counter is on average 210% overstating gym occupancy and so the default reduction of the real occupancy sensor has been set to 50%. Its worth noting that given the way occupancy is reported via the API, theres very little ability to do authentic time tracking or analysis or heuristic based results. |
| 🌡️ | **Busyness** | human friendly category displaying "how the gym feels" (eg. Quiet, Busy, and Crowded) based on density configurations measured against 36m2 square (6x6 meters around you). Defaults are Quiet = less than 1.5 people, Busy more than 1.5 people, Crowded more than 2.0 people. |
| 📈 | **Occupancy Trend** | reads "Getting Busier" or "Getting Quieter" depending on which way Real Occupancy is moving. |
| 🧑 | **Staffing Status** | provides an indication as to whether staff are in the gym or not. This is specifically important if you have a query or if you are a black card member who wishes to use the spa. |
| ⏰ | **Next Staff Status Change** | describes the timestamp when staff will either arrive or leave. Set an automation 30 minutes before it to catch the last of the black card spa amenities before staff leave (useful on weekends, when they tend to leave earlier). |
| 📐 | **Floor Area** | A simple passthrough value that shows you the club's square meter area as reported by the Planet Fitness website. It is raw and not altered (busyness dead-space subtraction is not applied.) |
| 🟠 | **Busy Threshold** | A simple calculated number based on specified thresholds on when the gym is considered busy (either based on default values or your own) |
| 🔴 | **Crowded Threshold** | A simple calculated number based on specified thresholds on when the gym is considered crowded (either based on default values or your own) |

</div>

## 📦 Install

1. HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/dancmorgan/pfau-occupancy-ha` as an **Integration**
3. Install **Planet Fitness AU Occupancy**, then restart Home Assistant

## ⚙️ Set up

**Settings → Devices & Services → Add Integration → "Planet Fitness AU Occupancy"**, then enter your Planet Fitness Australia member portal email and password.

## ⚙️ Options and Customisation

Reached the same way, via the integration's **Options** menu:

| Setting | Default | What it does |
| --- | :-: | --- |
| Polling interval | 5 min | How often the portal is queried |
| Occupancy reduction | 50% | How much to discount the reported count by |
| Busy / Crowded thresholds | 1.8 / 2.5 | What counts as busy, in people per 36 m² |
| Per-club threshold override | — | Same, but for one club that feels different |
| Occupancy Trend window | 60 min | How far back the busier/quieter line is fitted |

Floor area and staffed hours are **not** configurable here — see [Club list](#-club-list).

<table>
<tr>
<td>
<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/overrideclubselect.png" width="420" alt="Choosing a club to override">
<td>
<img src="https://raw.githubusercontent.com/dancmorgan/pfau-occupancy-ha/main/docs/integrationoptions.png" width="420" alt="General options">
</td>
</td>
</tr>
</table>

## 📈 Busyness, Density, and Trend

**Busyness** provides a more useful metric than Real or Reported Occupancy as it divides **Real Occupancy** by `area_sqm` and bands the result to give you a more useful "does the gym feel busy right now" category.

**Density** rather than occupancy is what makes clubs comparable - 60 people in a 1,600 m²
warehouse is quiet, the same 60 in a 400 m² studio is extremely busy.

Before dividing, 33% of `area_sqm` is subtracted as dead space - walkways, corridors, and the footprint of the racks and machines themselves, none of which is room a member can actually stand in. 

36 square metres - a 6x6 metre square - has been chosen as the "people per" metric to calculate density as it allows a user to mentally picture that box around them during a workout and decide their own threshold for how many people within it they consider too much.

Since these categories are subjective, they're fully user-configurable: set them generally in the integration's Options, or override just one club (Options → "Override a club's threshold") if it consistently feels busier or quieter than the general setting suggests. Leave both fields blank on that per-club form to remove the override again.

**Trend** indicates if the gym is actively filling or emptying so you know whether or not its going to be better or worse by the time you get there. There is some smoothing and deadening to ensure it does not flap. Works off any change greater than 2 of the Real Occupancy sensor over a period of 60 minutes (window and deadening configurable in settings). Defaults to "Getting Busier" on reboot or state loss.

## 👥 Club List

The portal's occupancy endpoint returns a club's name, address, current count and capacity limit - and nothing else. Opening hours and floor size aren't in it, or anywhere else in the API, so they come from [`clubs.yaml`](custom_components/pfau_occupancy/clubs.yaml) - pre-seeded with every AU club at the time of publishing. Until a club has an entry there, its Staffing, Floor Area, Busyness and threshold sensors report `unavailable`; the two occupancy sensors don't depend on it and work regardless.

If a new club opens or an existing club closes, please raise an issue or pull request for inclusion.

`clubs.yaml` is **refetched from this repo** by the running integration on restart and every 24 hours, so a correction or a newly-added club reaches everyone without waiting for the next release. If GitHub is unreachable when the integration starts, it falls back to the last successfully fetched copy (cached in your Home Assistant config directory), and if there's no cache yet either, to whatever version shipped with your installed release.

## 🔴 Disclaimer

Uses the member portal's internal, undocumented endpoints with your own membership credentials. Keep polling gentle; this may break if Planet Fitness changes the portal. Not affiliated with or endorsed by Planet Fitness.

<!-- Badges -->
[release-badge]: https://img.shields.io/github/v/release/dancmorgan/pfau-occupancy-ha?include_prereleases&style=for-the-badge&color=7B2CBF&labelColor=1C1C1C
[release-url]: https://github.com/dancmorgan/pfau-occupancy-ha/releases
[validate-badge]: https://img.shields.io/github/actions/workflow/status/dancmorgan/pfau-occupancy-ha/validate.yml?branch=main&style=for-the-badge&label=validate&color=3FB950&labelColor=1C1C1C
[validate-url]: https://github.com/dancmorgan/pfau-occupancy-ha/actions/workflows/validate.yml
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&labelColor=1C1C1C
[hacs-url]: https://hacs.xyz/docs/faq/custom_repositories
[ha-badge]: https://img.shields.io/badge/Home%20Assistant-2024.8%2B-41BDF5?style=for-the-badge&logo=homeassistant&logoColor=white&labelColor=1C1C1C
[ha-url]: https://www.home-assistant.io
[license-badge]: https://img.shields.io/github/license/dancmorgan/pfau-occupancy-ha?style=for-the-badge&color=FFD60A&labelColor=1C1C1C
[license-url]: LICENSE
[client-url]: https://github.com/dancmorgan/pfau-occupancy
