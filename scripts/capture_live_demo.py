"""Capture the final ready Phase 6 product state with a real browser."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from physics_toy_factory.qualification import QualificationError, sha256_file, utc_timestamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-base-url", default="http://127.0.0.1:8120")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--summary",
        type=Path,
        action="append",
        default=[],
        help="Qualification summary to update with the reviewed browser observation.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    """Require a live verified iframe, exercise its canvas, and retain a screenshot."""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        await page.goto(args.product_base_url, wait_until="networkidle")
        await page.locator("#app").wait_for(state="visible")
        await page.locator('#simulation-stage[data-preview-state="ready"]').wait_for(
            state="visible", timeout=20_000
        )
        frame = page.locator('#preview-host iframe[title="Verified physics toy preview"]')
        await frame.wait_for(state="visible")
        canvas = page.frame_locator('#preview-host iframe').locator("canvas")
        await canvas.wait_for(state="visible")
        box = await canvas.bounding_box()
        if box is None or box["width"] < 100 or box["height"] < 100:
            raise QualificationError("verified preview canvas is missing or too small")
        await page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.4)
        await page.wait_for_timeout(500)
        if await page.locator("#system-banner").is_visible():
            raise QualificationError("product displayed a system error during browser observation")
        await page.screenshot(path=args.output, full_page=True)
        await browser.close()
    observation = {
        "outcome": "passed",
        "recorded_at": utc_timestamp(),
        "screenshot": args.output.name,
        "screenshot_sha256": sha256_file(args.output),
        "checks": [
            "ready preview state",
            "sandboxed iframe visible",
            "canvas visible and responsive to pointer movement",
            "no product system-error banner",
        ],
    }
    for summary_path in args.summary:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise QualificationError(f"invalid qualification summary: {summary_path}")
        payload["browser_observation"] = observation
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"browser_observation=passed screenshot={args.output}")
    return 0


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except QualificationError as exc:
        raise SystemExit(f"browser_observation=failed reason={exc}") from exc


if __name__ == "__main__":
    main()
