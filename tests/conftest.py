"""Shared test configuration."""

from __future__ import annotations

from hypothesis import HealthCheck, settings

# Hypothesis charges the first draw in a fresh process for building the strategies
# themselves, and measures it against a wall-clock budget. Neither that nor a
# per-example deadline says anything about the code under test, and both flake on a
# cold or loaded CI runner -- which is where this suite spends most of its life.
settings.register_profile(
    "monoconf",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("monoconf")
