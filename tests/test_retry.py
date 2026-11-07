"""Unit tests for the fetch retry helper."""
from __future__ import annotations

import asyncio

import pytest

from conftest import load_module

retry_on = load_module("retry").retry_on


class Transient(Exception):
    """Stands in for PlanetFitnessConnectionError."""


class Fatal(Exception):
    """Stands in for PlanetFitnessAuthError."""


class Flaky:
    """Fails with `error` for the first `failures` calls, then returns "ok"."""

    def __init__(self, failures: int, error: Exception = Transient("502")) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def run(fetch, delays=(5, 15)):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    result = asyncio.run(retry_on(fetch, Transient, delays, sleep=fake_sleep))
    return result, slept


def test_success_first_try_does_not_sleep() -> None:
    fetch = Flaky(0)
    assert run(fetch) == ("ok", [])
    assert fetch.calls == 1


def test_recovers_after_one_blip() -> None:
    fetch = Flaky(1)
    assert run(fetch) == ("ok", [5])
    assert fetch.calls == 2


def test_recovers_on_the_last_attempt() -> None:
    fetch = Flaky(2)
    assert run(fetch) == ("ok", [5, 15])
    assert fetch.calls == 3


def test_persistent_failure_raises_after_all_attempts() -> None:
    fetch = Flaky(99)
    with pytest.raises(Transient):
        run(fetch)
    assert fetch.calls == 3


def test_other_errors_are_not_retried() -> None:
    fetch = Flaky(99, error=Fatal("bad credentials"))
    with pytest.raises(Fatal):
        run(fetch)
    assert fetch.calls == 1


def test_no_delays_means_a_single_attempt() -> None:
    fetch = Flaky(1)
    with pytest.raises(Transient):
        run(fetch, delays=())
    assert fetch.calls == 1
