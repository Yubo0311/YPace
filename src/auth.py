"""
Authentication module for https://epe.pku.edu.cn/venue/login

Login flow:
  1. Navigate to venue login page
  2. Click "统一身份认证登录（IAAA）" button
  3. Redirected to iaaa.pku.edu.cn — fill username + password
  4. Handle captcha if present on IAAA page
  5. Submit → redirected back to venue system
"""

from __future__ import annotations

from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .captcha import fill_captcha, solve_captcha

VENUE_LOGIN_URL = "https://epe.pku.edu.cn/venue/login"
IAAA_HOST = "iaaa.pku.edu.cn"

# ── Venue login page ───────────────────────────────────────────────────────────
# The button that triggers IAAA redirect
_SEL_IAAA_BTN = (
    "button:has-text('统一身份认证'), "
    "a:has-text('统一身份认证'), "
    ".el-button:has-text('IAAA'), "
    "button:has-text('IAAA')"
)

# ── IAAA login page (iaaa.pku.edu.cn) ─────────────────────────────────────────
_SEL_IAAA_USERNAME = "#user_name, input[name='userName'], input[name='username']"
_SEL_IAAA_PASSWORD = "#password, input[name='password'], input[type='password']"
_SEL_IAAA_SUBMIT   = "#logon_button, input[type='submit'], button[type='submit']"

# ── Post-login indicator (back on venue site) ──────────────────────────────────
_SEL_LOGGED_IN = (
    ".el-avatar, .user-info, [class*='user-name'], "
    "nav .username, .header-user, [class*='header'] .name"
)


async def login(page: Page, username: str, password: str, cjy_creds: dict | None = None) -> None:
    """Full login flow: venue page → IAAA → back to venue."""

    # ── Step 1: open venue login page ─────────────────────────────────────────
    logger.info(f"Opening venue login page: {VENUE_LOGIN_URL}")
    await page.goto(VENUE_LOGIN_URL, wait_until="networkidle", timeout=30_000)

    # ── Step 2: click the IAAA button ─────────────────────────────────────────
    logger.info("Clicking IAAA login button")
    iaaa_btn = page.locator(_SEL_IAAA_BTN).first
    try:
        await iaaa_btn.wait_for(state="visible", timeout=10_000)
        await iaaa_btn.click()
    except PlaywrightTimeout:
        await page.screenshot(path="screenshots/venue_login_page.png")
        raise RuntimeError(
            "Could not find the IAAA login button on the venue page. "
            "Screenshot saved to screenshots/venue_login_page.png — "
            "please check the selector in auth.py (_SEL_IAAA_BTN)."
        )

    # ── Step 3: wait for IAAA page to load ────────────────────────────────────
    logger.info("Waiting for IAAA login page...")
    try:
        await page.wait_for_url(
            lambda url: IAAA_HOST in url,
            timeout=15_000,
        )
    except PlaywrightTimeout:
        await page.screenshot(path="screenshots/iaaa_redirect_fail.png")
        raise RuntimeError(
            "Did not reach the IAAA login page after clicking the button. "
            "Screenshot saved to screenshots/iaaa_redirect_fail.png"
        )

    await page.wait_for_load_state("networkidle", timeout=15_000)
    logger.info(f"IAAA page loaded: {page.url}")

    # ── Step 4: fill credentials on IAAA page ────────────────────────────────
    logger.debug("Filling username on IAAA page")
    username_input = page.locator(_SEL_IAAA_USERNAME).first
    await username_input.wait_for(state="visible", timeout=10_000)
    await username_input.fill(username)

    logger.debug("Filling password on IAAA page")
    password_input = page.locator(_SEL_IAAA_PASSWORD).first
    await password_input.fill(password)

    # ── Step 5: handle captcha if present ────────────────────────────────────
    if await _check_captcha_present(page):
        logger.info("Captcha detected on IAAA page, solving...")
        answer = await solve_captcha(page, cjy_creds=cjy_creds)
        await fill_captcha(page, answer)

    # ── Step 6: submit ────────────────────────────────────────────────────────
    logger.info("Submitting IAAA login form")
    submit_btn = page.locator(_SEL_IAAA_SUBMIT).first
    await submit_btn.click()

    # ── Step 7: wait for redirect back to venue ───────────────────────────────
    logger.info("Waiting for redirect back to venue system...")
    try:
        await page.wait_for_url(
            lambda url: IAAA_HOST not in url and url.startswith("http"),
            timeout=20_000,
        )
        logger.info(f"Redirected to: {page.url}")
    except PlaywrightTimeout:
        await page.screenshot(path="screenshots/login_fail.png")
        raise RuntimeError(
            "Login did not complete — still on IAAA page after submit. "
            "Check credentials or captcha. "
            "Screenshot saved to screenshots/login_fail.png"
        )

    # ── Step 8: confirm we are logged into the venue system ───────────────────
    await page.wait_for_load_state("networkidle", timeout=15_000)
    logger.success("Login successful")


async def _check_captcha_present(page: Page) -> bool:
    """Return True if a captcha widget is visible on the current page."""
    captcha_selectors = [
        "img.captcha",
        "img[src*='captcha']",
        "img[src*='verify']",
        "img[alt*='验证码']",
        "[class*='captcha']",
        "[id*='captcha']",
        "#captcha_image",
    ]
    for selector in captcha_selectors:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


async def ensure_logged_in(page: Page, username: str, password: str) -> None:
    """
    Re-authenticate only if the session has expired.
    Call this before each booking attempt if the session might time out.
    """
    current_url = page.url

    # If we somehow ended up on the login page, log in again
    if not current_url.startswith("http") or "login" in current_url or IAAA_HOST in current_url:
        logger.info("Session not active, logging in...")
        await login(page, username, password)
        return

    # Quick check: is the logged-in indicator present?
    try:
        await page.wait_for_selector(_SEL_LOGGED_IN, timeout=3_000)
        logger.debug("Session still active")
    except PlaywrightTimeout:
        logger.info("Session may have expired, re-authenticating")
        await login(page, username, password)
