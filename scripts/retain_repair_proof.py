"""Retain one genuine live S17 red-to-green repair proof."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from physics_toy_factory.config import load_settings
from physics_toy_factory.qualification import QualificationError, RepairProofRunner


def parse_args() -> argparse.Namespace:
    """Parse explicit live-proof controls without accepting secrets on argv."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Product environment file; secrets stay in this ignored file.",
    )
    parser.add_argument(
        "--product-base-url",
        default="http://127.0.0.1:8120",
        help="Running product used to prove the workspace is idle and reset it.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=600,
        help="Overall live stream timeout.",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=0.50,
        help="Hard S17 repair-run budget; use 0 to omit the ceiling.",
    )
    parser.add_argument(
        "--publish-dir",
        type=Path,
        help="Optional selected-evidence directory; output is path-sanitized and secret-checked.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    """Run the proof and print only safe identifiers and hashes."""

    if args.timeout_seconds <= 0:
        raise QualificationError("timeout must be positive")
    if args.budget_usd < 0:
        raise QualificationError("budget must be nonnegative")
    settings = load_settings(env_file=args.env_file)
    runner = RepairProofRunner(
        settings,
        product_base_url=args.product_base_url,
        timeout_seconds=args.timeout_seconds,
        budget_usd=args.budget_usd or None,
    )
    async with asyncio.timeout(args.timeout_seconds):
        result = await runner.run(publish_dir=args.publish_dir)
    print(f"repair_proof=passed run_id={result.run_id} sketch_sha256={result.sketch_sha256}")
    print(f"artifact_dir={result.artifact_dir}")
    return 0


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (QualificationError, TimeoutError) as exc:
        raise SystemExit(f"repair_proof=failed reason={exc}") from exc


if __name__ == "__main__":
    main()
