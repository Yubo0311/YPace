"""
Core booking logic: navigate to venue, select date/slot, confirm booking.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .captcha import fill_captcha, solve_captcha, solve_click_captcha

# ── Selectors (confirmed from DOM inspection) ──────────────────────────────────
_SEL_VENUE_NAV   = ".tabItem:has-text('场地预约')"
_SEL_CAROUSEL_NEXT = (
    ".el-carousel__arrow--right, "
    "[class*='carousel'] [class*='next'], "
    "[class*='carousel'] [class*='right'], "
    ".swiper-button-next"
)
HOME_URL = "https://epe.pku.edu.cn/venue/home"

# Max green slots to click per order (rule: same venue ≤ 2 slots)
MAX_SLOTS_PER_ORDER = 2


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

async def book_venue(page: Page, venue_config: dict[str, Any], cjy_creds: dict | None = None) -> bool:
    """
    One full pass:
      1. Navigate to venue listing.
      2. Wait for carousel → click the venue card.
      3. On the booking page, try each date from book_days_ahead.
         For each date, click up to MAX_SLOTS_PER_ORDER available green slots
         that match priority_slots, tick the agreement checkbox, and submit.

    Returns True on successful booking, False otherwise.
    """
    venue_name: str       = venue_config["name"]
    priority_slots: list  = venue_config.get("priority_slots", [])
    book_days_ahead: list = venue_config.get("book_days_ahead", [1])

    logger.info(f"Attempting to book: {venue_name}")

    # ── Step 1: navigate to venue listing ────────────────────────────────────
    try:
        await _navigate_to_venue_listing(page)
    except Exception as e:
        logger.warning(f"Navigation failed: {e}")
        return False

    # ── Step 2: carousel → click venue card ──────────────────────────────────
    on_page = await _wait_and_click_venue(page, venue_name, timeout_sec=30)
    if not on_page:
        logger.warning(f"'{venue_name}' not found in carousel within 30 s")
        return False

    logger.debug(f"On booking page: {page.url}")
    await asyncio.sleep(1)

    # ── Step 3: iterate dates ─────────────────────────────────────────────────
    for days_ahead in book_days_ahead:
        target_date = date.today() + timedelta(days=days_ahead)
        logger.info(f"  Trying date: {target_date}")

        # Select the date tab
        selected = await _select_date(page, target_date)
        if not selected:
            logger.debug(f"  Date {target_date} not available in calendar")
            continue

        await asyncio.sleep(0.8)  # wait for slot table to refresh

        # Click available green slots matching priority_slots (≤ MAX per order)
        clicked = await _click_priority_slots(page, priority_slots)
        if clicked == 0:
            logger.debug(f"  No available slots on {target_date}")
            continue

        logger.info(f"  Clicked {clicked} slot(s) on {target_date}")

        # Tick "已阅读并同意预约须知"
        agreed = await _tick_agreement(page)
        if not agreed:
            logger.warning("  Could not tick agreement checkbox")
            # un-select slots and try next date
            await _unselect_slots(page)
            continue

        # Submit
        success = await _submit_order(page, cjy_creds=cjy_creds)
        if success:
            logger.success(f"Booking confirmed: {venue_name} on {target_date}")
            return True

        logger.debug(f"  Submit did not succeed on {target_date}")

    logger.debug(f"No available slot for {venue_name} this pass")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _navigate_to_venue_listing(page: Page) -> None:
    """Go to home page and click the '场地预约' tab."""
    logger.debug(f"→ home: {HOME_URL}")
    await page.goto(HOME_URL, wait_until="networkidle", timeout=20_000)

    nav = page.locator(_SEL_VENUE_NAV).first
    try:
        await nav.wait_for(state="visible", timeout=8_000)
        await nav.click()
        await page.wait_for_load_state("networkidle", timeout=10_000)
        await asyncio.sleep(1)
    except PlaywrightTimeout:
        await page.screenshot(path="screenshots/nav_fail.png")
        raise RuntimeError("Could not click '场地预约'. Screenshot: screenshots/nav_fail.png")


async def _wait_and_click_venue(page: Page, venue_name: str, timeout_sec: int = 30) -> bool:
    """Advance carousel until venue card is visible, then click it."""
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        card = page.get_by_text(venue_name, exact=True).first
        try:
            if await card.count() > 0 and await card.is_visible():
                await card.click()
                await page.wait_for_load_state("networkidle", timeout=10_000)
                logger.debug(f"Clicked venue card: {venue_name}")
                return True
        except Exception:
            pass
        await _advance_carousel(page)
        await asyncio.sleep(1.5)
    await page.screenshot(path="screenshots/carousel_timeout.png")
    return False


async def _advance_carousel(page: Page) -> None:
    try:
        btn = page.locator(_SEL_CAROUSEL_NEXT).first
        if await btn.count() > 0:
            await btn.click()
    except Exception:
        pass


async def _select_date(page: Page, target_date: date) -> bool:
    """
    Click the date tab in `.date_box` that matches target_date.
    Tabs contain text like "03月03日".
    Returns True if the tab was found and clicked.
    """
    month_str = f"{target_date.month:02d}月{target_date.day:02d}日"
    logger.debug(f"  Looking for date tab: '{month_str}'")

    try:
        # Dump available date tabs for debugging
        available_dates = await page.evaluate("""() => {
            const tabs = document.querySelectorAll('.date_box div, .date_box span');
            return Array.from(tabs)
                .map(el => (el.innerText || el.textContent || '').trim())
                .filter(t => t.length > 0);
        }""")
        logger.debug(f"  Available date tabs: {available_dates}")

        tab = page.locator(".date_box div").filter(has_text=month_str).first
        if await tab.count() == 0:
            logger.debug(f"  Date tab '{month_str}' not found")
            return False
        await tab.click()
        await asyncio.sleep(0.5)
        logger.debug(f"  Clicked date tab '{month_str}'")
        return True
    except Exception as e:
        logger.debug(f"  _select_date error: {e}")
        return False


async def _click_priority_slots(page: Page, priority_slots: list[str]) -> int:
    """
    Table structure (confirmed from DOM):
      Row 17-like: 场地 | 08:00-09:00 | 09:00-10:00 | ...  (time header)
      Row 9-like:  1号  | <reserveBlock free> | ...         (court + blocks)

    Available blocks have class 'reserveBlock position free'.
    The block index within a row matches the corresponding time header cell index.

    Strategy:
      1. Read the time header row to get current {time → cell_index} mapping.
      2. If priority times not visible, click the last cell of the header row
         (the '>' advance button) and retry — up to 25 times.
      3. Once target times visible, click the matching free blocks from the
         FIRST court row that has them (same row = same court, max 2 slots).
    """
    target_starts = [s.split("-")[0].strip() for s in priority_slots]

    async def _read_header() -> dict:
        """Return {start_time: cell_index} for current visible time header."""
        return await page.evaluate(r"""() => {
            const mapping = {};
            for (const tr of document.querySelectorAll('tr')) {
                if (!/\d{2}:\d{2}/.test(tr.innerText || '')) continue;
                Array.from(tr.querySelectorAll('td, th')).forEach((cell, i) => {
                    const m = (cell.innerText || cell.textContent || '').match(/(\d{2}:\d{2})/);
                    if (m) mapping[m[1]] = i;
                });
                break;
            }
            return mapping;
        }""")

    async def _advance() -> str | None:
        """
        Click the ivu-icon-ios-arrow-forward icon that lives inside the
        .arrowWrap div of the LAST visible time cell.  This is a Vue virtual
        column list — there is no CSS scroll; only clicking the icon works.
        """
        return await page.evaluate(r"""() => {
            // Primary: the '>' icon sits inside .arrowWrap in the last time cell
            const icon = document.querySelector(
                '.arrowWrap .ivu-icon-ios-arrow-forward'
            );
            if (icon) { icon.click(); return 'arrowWrap-forward'; }

            // Fallback: any forward icon carrying the Vue component attribute
            const icon2 = document.querySelector(
                '[data-v-e6c914f0].ivu-icon-ios-arrow-forward'
            );
            if (icon2) { icon2.click(); return 'data-v-forward'; }

            return null;
        }""")

    # Scroll until at least one priority time is visible
    prev_header: dict = {}
    stall = 0
    for i in range(30):
        header = await _read_header()
        logger.debug(f"  [scroll {i}] visible={sorted(header)}  method pending")
        if any(t in header for t in target_starts):
            break
        stall = (stall + 1) if header == prev_header else 0
        prev_header = header
        if stall >= 3:
            logger.debug("  Header unchanged for 3 iterations, giving up")
            break
        method = await _advance()
        logger.debug(f"    advance → {method}")
        if not method:
            break
        await asyncio.sleep(0.3)
    else:
        header = await _read_header()

    target_cells = [header[t] for t in target_starts if t in header]
    logger.debug(f"  target_cells={target_cells}  header={header}")

    if not target_cells:
        logger.debug("  No priority times visible after scrolling")
        return 0

    clicked = await page.evaluate(
        r"""([cellIndices, maxSlots]) => {
            const courtRows = Array.from(document.querySelectorAll('tr')).filter(
                tr => tr.querySelectorAll('.reserveBlock').length > 0
            );
            for (const tr of courtRows) {
                const cells = tr.querySelectorAll('td, th');
                const toClick = [];
                for (const idx of cellIndices) {
                    if (toClick.length >= maxSlots) break;
                    const block = cells[idx]?.querySelector('.reserveBlock');
                    if (!block) continue;
                    const cls = block.className || '';
                    if (!cls.includes('free')) continue;
                    if (cls.includes('disabled') || cls.includes('active') ||
                        cls.includes('selected')) continue;
                    toClick.push(block);
                }
                if (!toClick.length) continue;
                toClick.forEach(b => b.click());
                return toClick.length;
            }
            return 0;
        }""",
        [target_cells, MAX_SLOTS_PER_ORDER],
    )

    if clicked:
        logger.debug(f"  Clicked {clicked} slot(s)")
    else:
        logger.debug("  No free blocks found at target cell indices")
    return clicked


async def _unselect_slots(page: Page) -> None:
    """Click any selected (active) reserveBlock to deselect before giving up."""
    try:
        await page.evaluate("""() => {
            document.querySelectorAll('.reserveBlock.active, .reserveBlock.selected')
                .forEach(el => el.click());
        }""")
    except Exception:
        pass


async def _tick_agreement(page: Page) -> bool:
    """
    Tick the '已阅读并同意预约须知' checkbox.
    The label has class 'ivu-checkbox-wrapper'; click the inner checkbox input.
    """
    try:
        checkbox = page.locator(
            "label.ivu-checkbox-wrapper:has-text('已阅读') input[type='checkbox']"
        ).first
        if await checkbox.count() == 0:
            # Fallback: click the label itself
            label = page.locator(
                "label.ivu-checkbox-wrapper:has-text('已阅读')"
            ).first
            if await label.count() > 0:
                await label.click()
                logger.debug("  Ticked agreement (label click)")
                return True
            return False

        is_checked = await checkbox.is_checked()
        if not is_checked:
            await checkbox.click()
            await asyncio.sleep(0.3)
        logger.debug("  Agreement checkbox ticked")
        return True
    except Exception as e:
        logger.debug(f"  _tick_agreement error: {e}")
        return False


async def _submit_order(page: Page, cjy_creds: dict | None = None) -> bool:
    """
    Click the submit button, handle the click-character captcha modal if it
    appears, then wait for a success indicator.
    """
    try:
        submit_btn = page.locator(
            ".submit_order_box .btn:not(.disab):not(.cancel)"
        ).first
        if await submit_btn.count() == 0:
            logger.debug("  Submit button not found or still disabled")
            return False

        text = await submit_btn.inner_text()
        logger.debug(f"  Clicking submit button: '{text.strip()}'")
        await submit_btn.click()
        await asyncio.sleep(1.5)

        # Handle click-character captcha popup if it appears
        await solve_click_captcha(page, cjy_creds=cjy_creds)
        await asyncio.sleep(1.0)

        # After captcha the page navigates to the payment page (?tradeNo=...)
        # Click the 支付 button (not 取消订单)
        return await _pay_order(page)
    except Exception as e:
        logger.warning(f"  _submit_order error: {e}")
        return False


async def _pay_order(page: Page) -> bool:
    """
    On the payment confirmation page, click the 支付 button and wait
    for a success indicator.  The button text is '支付' or '支付（NNNs）'.
    """
    # Wait for payment page to load (URL contains tradeNo or page has 支付 button)
    try:
        await page.wait_for_selector(
            "button:has-text('支付'), div.btn:has-text('支付'), "
            "a:has-text('支付'), :text('请您支付')",
            timeout=10_000,
        )
    except PlaywrightTimeout:
        # Maybe already on a success page, or captcha was not shown
        if any(k in page.url for k in ("tradeNo", "success", "pay")):
            logger.debug(f"  On payment/success page: {page.url}")
            # Still try to click pay if button exists
        else:
            await page.screenshot(path="screenshots/submit_result.png")
            logger.debug("  Payment page not detected. Screenshot: screenshots/submit_result.png")
            return False

    # Find and click the 支付 button (exclude 取消订单)
    pay_btn = page.locator(
        "button:has-text('支付'), .btn:has-text('支付')"
    ).filter(has_not_text="取消").first
    if await pay_btn.count() == 0:
        # Fallback: any element with text 支付 that isn't cancel
        pay_btn = page.get_by_text("支付", exact=False).filter(has_not_text="取消").first

    if await pay_btn.count() == 0:
        logger.debug("  支付 button not found")
        return False

    btn_text = await pay_btn.inner_text()
    logger.info(f"  Clicking pay button: '{btn_text.strip()}'")
    await pay_btn.click()
    await asyncio.sleep(2.0)

    # Success: page navigates away from payment page, or shows confirmation
    success_sel = ":text('支付成功'), :text('预约成功'), :text('已完成'), :text('订单完成')"
    try:
        await page.wait_for_selector(success_sel, timeout=8_000)
        logger.success("Payment confirmed!")
        return True
    except PlaywrightTimeout:
        # ¥0 orders may complete immediately without an explicit success message
        if "tradeNo" not in page.url:
            logger.success(f"Payment complete, page navigated to: {page.url}")
            return True
        await page.screenshot(path="screenshots/pay_result.png")
        logger.debug("  No payment success indicator. Screenshot: screenshots/pay_result.png")
        return False
