"""Navigation politeness helpers (spec §36).

Conservative pacing between requests: a base delay plus random jitter, with a hook to slow
down after rate-limit signals. Randomness is injectable so tests stay deterministic.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable


def polite_delay(
    base_seconds: float,
    jitter_seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> float:
    """Sleep for ``base + U(0, jitter)`` seconds; return the delay used (spec §36)."""
    r = rng or random
    delay = max(0.0, base_seconds) + r.uniform(0, max(0.0, jitter_seconds))
    sleep(delay)
    return delay


class RateLimitBackoff:
    """Multiplicative slowdown after rate-limit signals (spec §36)."""

    def __init__(self, factor: float = 2.0, max_multiplier: float = 8.0) -> None:
        self.factor = factor
        self.max_multiplier = max_multiplier
        self.multiplier = 1.0

    def on_rate_limited(self) -> None:
        self.multiplier = min(self.max_multiplier, self.multiplier * self.factor)

    def on_success(self) -> None:
        # Decay slowly back toward normal pacing.
        self.multiplier = max(1.0, self.multiplier / self.factor)

    def scale(self, base_delay: float) -> float:
        return base_delay * self.multiplier
