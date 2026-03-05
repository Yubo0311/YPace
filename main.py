"""
PKU venue auto-booking script.

Flow:
  1. Parse config & credentials.
  2. Calculate when booking opens today (booking_open_time).
  3. Sleep until (open_time - pre_login_minutes) → launch browser & login.
  4. Sleep until exact open_time.
  5. Navigate directly to venue URL, select slots, handle captcha, pay.

Usage:
    python main.py [--config config.yaml] [--credentials credentials.env]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger
from playwright.async_api import async_playwright

from src.auth import login
from src.booker import (
    _select_date,
    _click_priority_slots,
    _tick_agreement,
    _submit_order,
)
from src.config_loader import get_enabled_venues, load_config, load_credentials

# ── Logging ────────────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="DEBUG",
)
Path("logs").mkdir(exist_ok=True)
logger.add("logs/booking.log", rotation="10 MB", retention="7 days", level="DEBUG")
Path("screenshots").mkdir(exist_ok=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PKU venue auto-booking")
    p.add_argument("--config",      default="config.yaml")
    p.add_argument("--credentials", default="credentials.env")
    return p.parse_args()


def _open_dt(open_time_str: str) -> datetime:
    """Return today's datetime for the given HH:MM string."""
    h, m = map(int, open_time_str.split(":"))
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


async def _wait_until(target: datetime) -> None:
    """Sleep until target datetime, logging countdowns."""
    while True:
        now = datetime.now()
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        if remaining > 60:
            logger.info(f"  Waiting {remaining/60:.1f} min until {target.strftime('%H:%M:%S')} ...")
            await asyncio.sleep(min(remaining - 30, 30))
        elif remaining > 5:
            logger.info(f"  {remaining:.0f}s until {target.strftime('%H:%M:%S')} ...")
            await asyncio.sleep(1)
        else:
            await asyncio.sleep(0.1)


async def _book_venue_direct(
    page,
    venue: dict,
    cjy_creds: dict | None,
) -> bool:
    """
    Navigate directly to the venue reservation URL, then select slots,
    agree and submit.  Returns True on successful booking.
    """
    venue_id   = venue["venue_id"]
    venue_name = venue["name"]
    priority_slots  = venue.get("priority_slots", [])
    book_days_ahead = venue.get("book_days_ahead", [0])

    url = f"https://epe.pku.edu.cn/venue/venue-reservation/{venue_id}"
    logger.info(f"→ {url}")
    await page.goto(url, wait_until="networkidle", timeout=20_000)
    await page.wait_for_timeout(1000)

    for days_ahead in book_days_ahead:
        target = date.today() + timedelta(days=days_ahead)
        logger.info(f"  Trying date: {target}")

        selected = await _select_date(page, target)
        if not selected:
            logger.debug(f"  Date {target} not available")
            continue

        await asyncio.sleep(0.8)

        clicked = await _click_priority_slots(page, priority_slots)
        if clicked == 0:
            logger.debug(f"  No available slots on {target}")
            continue

        logger.info(f"  Selected {clicked} slot(s) — ticking agreement & submitting")
        if not await _tick_agreement(page):
            logger.warning("  Could not tick agreement checkbox")
            continue

        success = await _submit_order(page, cjy_creds=cjy_creds)
        if success:
            logger.success(f"Booking confirmed: {venue_name} on {target}")
            return True

        logger.warning("  Submit did not succeed, trying next date")

    return False


async def main() -> None:
    args = parse_args()

    try:
        credentials = load_credentials(args.credentials)
        config      = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        sys.exit(1)

    enabled_venues = get_enabled_venues(config)
    if not enabled_venues:
        logger.warning("No enabled venues in config.yaml")
        sys.exit(0)

    # Validate venue_id present
    for v in enabled_venues:
        if "venue_id" not in v:
            logger.error(f"venue '{v['name']}' is missing 'venue_id' in config.yaml")
            sys.exit(1)

    open_time_str    = config.get("booking_open_time", "10:00")
    pre_login_min    = config.get("pre_login_minutes", 3)
    headless         = config.get("headless", False)
    cjy_creds        = credentials.get("cjy_creds")

    open_dt    = _open_dt(open_time_str)
    login_dt   = open_dt - timedelta(minutes=pre_login_min)
    now        = datetime.now()

    # If open time already passed today, warn and run immediately
    if open_dt < now:
        logger.warning(
            f"Booking open time {open_time_str} has already passed today "
            f"({now.strftime('%H:%M:%S')}). Running immediately."
        )
        login_dt = now

    logger.info(
        f"Schedule: login at {login_dt.strftime('%H:%M:%S')}, "
        f"book at {open_dt.strftime('%H:%M:%S')}  "
        f"| venues: {[v['name'] for v in enabled_venues]}"
    )

    # ── Phase 1: wait until login time ────────────────────────────────────────
    await _wait_until(login_dt)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = await context.new_page()

        # ── Phase 2: login ─────────────────────────────────────────────────
        logger.info("Logging in...")
        try:
            await login(
                page,
                credentials["username"],
                credentials["password"],
                cjy_creds=cjy_creds,
            )
        except RuntimeError as e:
            logger.error(f"Login failed: {e}")
            await browser.close()
            sys.exit(1)

        # ── Phase 3: wait for exact booking open time ──────────────────────
        await _wait_until(open_dt)
        logger.info(f"Booking window open! Starting now...")

        # ── Phase 4: book each enabled venue ──────────────────────────────
        for venue in enabled_venues:
            success = await _book_venue_direct(page, venue, cjy_creds)
            if success:
                break
            logger.warning(f"Could not book {venue['name']}, trying next venue")

        logger.info("Done. Closing browser in 5 seconds...")
        await asyncio.sleep(5)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
