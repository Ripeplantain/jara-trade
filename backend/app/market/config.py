"""Market data settings. The only module that reads the environment."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketSettings:
    massive_api_key: str | None = None
    massive_poll_interval: float = 15.0  # seconds, snapshot mode (paid plans)
    massive_eod_poll_interval: float = 900.0  # seconds, end-of-day mode (free plan)
    simulator_interval: float = 0.5  # seconds per tick
    simulator_seed: int | None = None  # fixed seed for reproducible demos and tests

    @property
    def use_massive(self) -> bool:
        return bool(self.massive_api_key)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MarketSettings:
        env = os.environ if env is None else env
        key = env.get("MASSIVE_API_KEY", "").strip() or None
        seed = env.get("SIMULATOR_SEED", "").strip()
        return cls(
            massive_api_key=key,
            massive_poll_interval=float(env.get("MASSIVE_POLL_INTERVAL", "15") or 15),
            simulator_seed=int(seed) if seed else None,
        )
