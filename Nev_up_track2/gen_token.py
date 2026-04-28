#!/usr/bin/env python3
"""Mint a JWT for a seed-data trader.

Usage examples (run inside the container or with PYTHONPATH=/app):

    python scripts/gen_token.py
        # → prints a token for Alex Mercer (revenge_trading) by default

    python scripts/gen_token.py --name "Casey Kim"
    python scripts/gen_token.py --user-id f412f236-4edc-47a2-8f54-8763a6ed2ce8

The output is just the token on stdout, suitable for shell substitution:

    TOKEN=$(python scripts/gen_token.py --name "Sam Rivera")
    curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/users/.../profile
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import issue_token  # noqa: E402
from app.seed import seed_store  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Mint a JWT for a seed trader.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--user-id", help="Trader userId (UUID).")
    g.add_argument("--name", help='Trader display name, e.g. "Casey Kim".')
    p.add_argument(
        "--ttl",
        type=int,
        default=86400,
        help="Token TTL in seconds (default 24h).",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List all seed traders and exit.",
    )
    args = p.parse_args()

    seed_store.load()

    if args.list:
        print(f"{'name':<14} userId")
        print("-" * 60)
        for t in seed_store.all_traders():
            print(f"{t['name']:<14} {t['userId']}")
        return 0

    user_id = args.user_id
    name = args.name or "Alex Mercer"

    if user_id is None:
        match = next(
            (t for t in seed_store.all_traders() if t["name"] == name), None
        )
        if not match:
            print(f"No seed trader named {name!r}", file=sys.stderr)
            return 2
        user_id = match["userId"]
        name = match["name"]
    else:
        match = seed_store.trader(user_id)
        name = match["name"] if match else "unknown"

    token = issue_token(user_id, name=name, ttl_seconds=args.ttl)
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
