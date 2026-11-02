"""Fakes for the Home Assistant integration tests.

Only imported where pytest-homeassistant-custom-component is installed (Linux
or macOS; its harness does not run on Windows). The portal and the GitHub
clubs.yaml fetch are faked at the HTTP level, so the real PlanetFitnessClient,
retry helper and coordinator all run for real.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
    AiohttpClientMocker,
)
from yarl import URL

from custom_components.pfau_occupancy.const import CLUB_DATA_URL

EMAIL = "Member@Example.com"
PASSWORD = "hunter2"

PORTAL = "https://members.planetfitnessaustralia.com.au"
LOGIN_URL = f"{PORTAL}/login"
STUDIOS_URL = f"{PORTAL}/studio-api/v1/public/studios/forwhitelabelportal"
COUNT_URL = re.compile(
    re.escape(PORTAL) + r"/nox/v1/studios/(?P<studio_id>\d+)/utilization/v2/active-checkin$"
)
_JSON_HEADERS = {"Content-Type": "application/json"}

# Monday 10:00 in Brisbane (UTC+10), 11:00 in Sydney (AEDT, UTC+11).
MONDAY_MORNING = "2026-10-19 00:00:00+00:00"

TEST_CLUBS_YAML = """
clubs:
  morayfield:
    name: Morayfield
    area_sqm: 1000
    timezone: Australia/Brisbane
    staffed:
      weekdays:
        - 09:00-13:00
        - 15:00-19:00
  eastside:
    area_sqm: 500
    timezone: Australia/Sydney
    open:
      daily: 05:00-21:00
    staffed:
      weekdays: 09:00-17:00
  badzone:
    area_sqm: 400
    timezone: Mars/Olympus
"""


def club_json(name: str, occupancy: int | None, limit: int = 200) -> dict[str, Any]:
    """One club in the fake portal: a name and its live count.

    `limit` is kept so tests can describe a club fully, but the new portal
    does not report one.
    """
    return {"name": name, "occupancy": occupancy, "limit": limit}


def default_clubs() -> list[dict[str, Any]]:
    return [
        club_json("Morayfield", 40),
        club_json("Eastside", 30, limit=100),
        club_json("Mystery Club", 10, limit=100),
        club_json("Badzone", 20, limit=100),
    ]


def _response(method: str, url: str, **kwargs: Any) -> AiohttpClientMockResponse:
    return AiohttpClientMockResponse(method, URL(url), **kwargs)


class Portal:
    """The member portal: login, the studio list, then a live count per studio.

    A poll is one request for the studio list plus one count request per
    club. `outcomes` queues what the next count requests do before they go
    back to answering normally: an int is an HTTP status to return, an
    exception is raised from the request. Use `fail_polls` to fail whole
    polls rather than individual requests.
    """

    def __init__(self, mock: AiohttpClientMocker) -> None:
        self.clubs = default_clubs()
        self.outcomes: list[int | Exception] = []
        self.login_status = 200
        self.login_body = ""
        self.logins = 0
        self.count_requests = 0
        self._studio_ids: dict[str, int] = {}
        mock.post(LOGIN_URL, side_effect=self._login)
        mock.get(STUDIOS_URL, side_effect=self._studios)
        mock.get(COUNT_URL, side_effect=self._count)

    @property
    def fetches(self) -> int:
        """Poll attempts so far, counting each attempt's per-club requests."""
        return self.count_requests // max(1, len(self.clubs))

    def _studio_id(self, name: str) -> int:
        return self._studio_ids.setdefault(name, 1_000_000 + len(self._studio_ids))

    async def _login(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        self.logins += 1
        if self.login_status != 200:
            return _response(
                method, str(url), status=self.login_status, text=self.login_body
            )
        return _response(
            method, str(url), cookies={"SESSION": f"session-{self.logins}"}
        )

    async def _studios(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        studios = [
            {
                "magiclineId": self._studio_id(club["name"]),
                "name": club["name"],
                "zoneId": "Australia/Brisbane",
                "address": {"houseNumber": "1", "street": f"{club['name']} Street"},
            }
            for club in self.clubs
        ]
        return _response(
            method, str(url), json={"content": studios}, headers=_JSON_HEADERS
        )

    async def _count(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        self.count_requests += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            return _response(method, str(url), status=outcome, text="Bad Gateway")
        studio_id = int(COUNT_URL.search(str(url))["studio_id"])
        by_id = {self._studio_id(c["name"]): c for c in self.clubs}
        club = by_id.get(studio_id)
        if club is None or club["occupancy"] is None:
            # A studio whose count cannot be read.
            return _response(method, str(url), status=502, text="Bad Gateway")
        return _response(
            method,
            str(url),
            json={"value": club["occupancy"]},
            headers=_JSON_HEADERS,
        )

    def fail_polls(self, *failures: int | Exception) -> None:
        """Make the next len(failures) poll attempts fail outright.

        Each attempt's count requests all fail with the given status or
        error, so the client treats the whole poll as failed.
        """
        self.outcomes = [
            failure for failure in failures for _ in range(len(self.clubs))
        ]

    def go_down(self) -> None:
        """Every count request fails with a 502 until `recover` is called."""
        self.outcomes = [502] * 10_000

    def recover(self) -> None:
        self.outcomes = []


class RemoteClubData:
    """The clubs.yaml that the integration refetches from GitHub."""

    def __init__(self, mock: AiohttpClientMocker) -> None:
        self.text = TEST_CLUBS_YAML
        self.status = 200
        self.error: Exception | None = None
        mock.get(CLUB_DATA_URL, side_effect=self._get)

    async def _get(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        if self.error is not None:
            raise self.error
        return _response(method, str(url), status=self.status, text=self.text)


def client_error() -> aiohttp.ClientError:
    return aiohttp.ClientError("portal unreachable")


async def refresh(hass: HomeAssistant, freezer: Any, minutes: float = 6) -> None:
    """Advance the clock past the poll interval and let the update land."""
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    # Scheduled polls run as background tasks, which block_till_done skips
    # unless asked, so a retrying poll would otherwise be cut off mid-way.
    await hass.async_block_till_done(wait_background_tasks=True)
