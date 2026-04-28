"""Seed data access.

The deck is explicit: the eval harness must read the JSON as-is, no
modifications. So we treat the seed file as an immutable source of truth and
load it once at startup into module-level structures keyed by userId.

This is intentionally NOT in the database. The database stores session
memories that the coach generates over time; the seed JSON is the trader's
historical trade record (Track 1's territory). For Track 2, the seed is
read-only ground truth used by the profiler, eval harness, and audit lookups.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings

log = logging.getLogger("nevup.seed")


class SeedStore:
    """Indexed view of the seed dataset. All lookups are O(1)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.raw: dict = {}
        self.traders_by_id: dict[str, dict] = {}
        self.sessions_by_id: dict[str, dict] = {}
        self.trades_by_id: dict[str, dict] = {}
        # session_id → user_id (used for membership checks during audit)
        self.session_to_user: dict[str, str] = {}

    def load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(
                f"Seed dataset not found at {self.path}. "
                f"Bake seed_data/nevup_seed_dataset.json into the image."
            )
        with self.path.open() as f:
            self.raw = json.load(f)

        for trader in self.raw.get("traders", []):
            uid = trader["userId"]
            self.traders_by_id[uid] = trader
            for s in trader.get("sessions", []):
                sid = s["sessionId"]
                self.sessions_by_id[sid] = s
                self.session_to_user[sid] = uid
                for t in s.get("trades", []):
                    self.trades_by_id[t["tradeId"]] = t

        log.info(
            json.dumps(
                {
                    "event": "seed.loaded",
                    "traders": len(self.traders_by_id),
                    "sessions": len(self.sessions_by_id),
                    "trades": len(self.trades_by_id),
                }
            )
        )

    # ------------------------------------------------------------------ #
    # Lookups
    # ------------------------------------------------------------------ #
    def trader(self, user_id: str) -> dict | None:
        return self.traders_by_id.get(user_id)

    def session(self, session_id: str) -> dict | None:
        return self.sessions_by_id.get(session_id)

    def trade(self, trade_id: str) -> dict | None:
        return self.trades_by_id.get(trade_id)

    def user_owns_session(self, user_id: str, session_id: str) -> bool:
        return self.session_to_user.get(session_id) == user_id

    def all_traders(self) -> list[dict]:
        return list(self.traders_by_id.values())


# Module-level singleton — populated by main.py at startup.
seed_store = SeedStore(settings.seed_data_path)
