"""
Captcha handling.

Two modes:
  - Text captcha (login): codetype 1902, returns text → fill into input
  - Click captcha (submit): codetype 9801, returns x,y coordinates → click on image

If CHAOJIYING_* credentials are set, both are solved automatically.
Otherwise falls back to manual terminal input.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from pathlib import Path

import httpx
from loguru import logger
from playwright.async_api import ElementHandle, Page

_CJY_URL      = "https://upload.chaojiying.net/Upload/Processing.php"
_CJY_REPORT   = "https://upload.chaojiying.net/Upload/ReportError.php"
_TYPE_TEXT     = "1902"   # 4-6 char alphanumeric (login captcha)
_TYPE_CLICK    = "9801"   # click-character captcha (submit captcha)


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


async def _cjy_post(data: dict, timeout: int = 20) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_CJY_URL, data=data)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"超级鹰 request failed: {e}")
        return None


# ── Text captcha (login) ──────────────────────────────────────────────────────

async def solve_captcha(page: Page, cjy_creds: dict | None = None) -> str:
    """
    Screenshot the captcha element, auto-solve via 超级鹰 (codetype 1902)
    if credentials provided, otherwise prompt terminal input.
    """
    screenshot_dir = Path("screenshots")
    screenshot_dir.mkdir(exist_ok=True)
    captcha_path = screenshot_dir / f"captcha_{int(time.time())}.png"

    captcha_selectors = [
        "img.captcha", "img[src*='captcha']", "img[src*='verify']",
        "img[alt*='验证码']", "[class*='captcha'] img", "[id*='captcha'] img",
    ]
    captcha_element = None
    for sel in captcha_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                captcha_element = el
                break
        except Exception:
            continue

    if captcha_element:
        image_bytes = await captcha_element.screenshot(path=str(captcha_path))
    else:
        await page.screenshot(path=str(captcha_path))
        image_bytes = captcha_path.read_bytes()
        logger.warning("Captcha element not found; using full-page screenshot")

    if cjy_creds:
        result = await _cjy_post({
            "user":       cjy_creds["username"],
            "pass2":      _md5(cjy_creds["password"]),
            "softid":     cjy_creds["softid"],
            "codetype":   _TYPE_TEXT,
            "file_base64": base64.b64encode(image_bytes).decode(),
        })
        if result and result.get("err_no") == 0:
            answer = result["pic_str"].strip()
            logger.debug(f"超级鹰 text recognised: '{answer}'")
            return answer
        logger.warning("超级鹰 text solve failed, falling back to manual input")

    print(f"\n[验证码] 截图: {captcha_path.resolve()}")
    return input("[验证码] 请输入验证码: ").strip()


async def fill_captcha(
    page: Page,
    answer: str,
    input_selector: str = "input[placeholder*='验证码']",
) -> None:
    """Type the text captcha answer into the input field."""
    for sel in (
        input_selector,
        "input[name='captcha']",
        "input[name='verifyCode']",
        "input[id*='captcha']",
        "input[class*='captcha']",
    ):
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.fill(answer)
                logger.debug(f"Filled text captcha using: {sel}")
                return
        except Exception:
            continue
    raise RuntimeError("Could not find captcha input field.")


# ── Click captcha (submit modal) ──────────────────────────────────────────────

async def solve_click_captcha(
    page: Page,
    cjy_creds: dict | None = None,
) -> bool:
    """
    Handle the click-character captcha modal that appears after submitting
    an order.

    Modal structure (confirmed from screenshot):
      - Image: inside the modal, the main captcha graphic
      - Instruction text: "请依次点击【界，旧，句】"
      - After clicking all chars, modal closes automatically

    With 超级鹰 (codetype 9801):
      - Send image + str_debug="{8a:界,旧,句/8a}"
      - Response pic_str: "x1,y1|x2,y2|x3,y3"  (relative to image top-left)
      - Click each coordinate offset from the image's bounding box

    Without creds: take a screenshot and ask user to click manually (headless=False),
    then wait for the modal to close.

    Returns True if modal was handled (closed), False if not found.
    """
    # Detect the captcha modal
    modal_sel = "div:has-text('请完成安全验证'), div:has-text('安全验证')"
    try:
        await page.wait_for_selector(modal_sel, timeout=5_000)
    except Exception:
        return False  # no modal appeared

    logger.info("点选验证码 modal detected")

    # Dump the verifybox DOM so we can see every visual element
    dump = await page.evaluate(r"""() => {
        const top = document.querySelector('.verifybox-top');
        const root = top ? (top.parentElement || top) : document.body;
        const out = [];
        for (const el of root.querySelectorAll('*')) {
            const tag = el.tagName;
            if (!['IMG','CANVAS','DIV','SPAN'].includes(tag)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 10 || r.height < 10) continue;
            out.push({
                tag,
                cls: (el.className || '').slice(0, 60),
                w: Math.round(r.width),
                h: Math.round(r.height),
                x: Math.round(r.x),
                y: Math.round(r.y),
            });
        }
        return out;
    }""")
    logger.debug("  verifybox children:")
    for item in dump:
        logger.debug(f"    {item['tag']} cls='{item['cls']}' "
                     f"size={item['w']}x{item['h']} pos=({item['x']},{item['y']})")

    # Find the challenge element: largest canvas or img inside the verifybox
    target_handle = await page.evaluate_handle(r"""() => {
        const top = document.querySelector('.verifybox-top');
        const root = top ? (top.parentElement || top) : document.body;
        let best = null, bestArea = 0;
        for (const el of root.querySelectorAll('canvas, img')) {
            const r = el.getBoundingClientRect();
            const area = r.width * r.height;
            if (area > bestArea) { bestArea = area; best = el; }
        }
        return best;
    }""")
    if not target_handle or await page.evaluate("el => !el", target_handle):
        logger.warning("Could not locate captcha image inside modal")
        return False

    img_el = target_handle.as_element()
    bbox = await img_el.bounding_box()
    tag = await page.evaluate("el => el.tagName", target_handle)
    logger.debug(f"  Captcha element: {tag}  bbox={bbox}")

    # Read instruction text to extract the target characters
    instruction_text = await page.evaluate(r"""() => {
        for (const el of document.querySelectorAll('*')) {
            const txt = el.innerText || '';
            if (/请依次点击/.test(txt) && txt.length < 40) return txt;
        }
        return '';
    }""")
    logger.debug(f"Captcha instruction: '{instruction_text}'")

    # Parse characters from 【界，旧，句】
    chars_match = re.search(r'【([^】]+)】', instruction_text)
    chars = []
    if chars_match:
        raw = chars_match.group(1)
        # Split on ，（full-width or half-width comma）
        chars = [c.strip() for c in re.split(r'[，,]', raw) if c.strip()]
    logger.debug(f"Target chars: {chars}")

    if cjy_creds and chars:
        screenshot_dir = Path("screenshots")
        screenshot_dir.mkdir(exist_ok=True)
        img_path = screenshot_dir / f"click_captcha_{int(time.time())}.png"
        # scale="css" → 1 CSS pixel = 1 image pixel, so 超级鹰 coords map directly
        img_bytes = await img_el.screenshot(path=str(img_path), scale="css")
        logger.debug(f"  Captcha image saved: {img_path}  bbox={bbox}")

        str_debug = "{8a:" + ",".join(chars) + "/8a}"
        result = await _cjy_post({
            "user":       cjy_creds["username"],
            "pass2":      _md5(cjy_creds["password"]),
            "softid":     cjy_creds["softid"],
            "codetype":   _TYPE_CLICK,
            "file_base64": base64.b64encode(img_bytes).decode(),
            "str_debug":  str_debug,
        })
        logger.debug(f"超级鹰 click result: {result}")

        if result and result.get("err_no") == 0:
            coords_str = result.get("pic_str", "")
            if bbox and coords_str:
                for pair in coords_str.split("|"):
                    pair = pair.strip()
                    if not pair:
                        continue
                    x_rel, y_rel = (float(v) for v in pair.split(","))
                    # Coordinates from 超级鹰 are in CSS-pixel space (scale="css")
                    # so we can add directly to bbox origin and use page.mouse.click
                    # (page.mouse.click bypasses Playwright actionability/intercept check)
                    page_x = bbox["x"] + x_rel
                    page_y = bbox["y"] + y_rel
                    await page.mouse.click(page_x, page_y)
                    logger.debug(f"  Clicked page ({page_x:.0f}, {page_y:.0f})")
                    await page.wait_for_timeout(500)

                # Wait for modal to close OR page to navigate (both mean success)
                url_before = page.url
                try:
                    await page.wait_for_selector(
                        modal_sel, state="hidden", timeout=8_000
                    )
                    logger.info("点选验证码 solved, modal closed")
                    return True
                except Exception as e:
                    # If the page navigated away, the captcha was accepted
                    if page.url != url_before:
                        logger.info(f"点选验证码 solved, page navigated → {page.url}")
                        return True
                    # Page closed means it navigated to a new page (e.g. payment)
                    if "closed" in str(e).lower():
                        logger.info("点选验证码 solved, page context changed (navigated)")
                        return True
                    logger.warning(f"Modal did not close: {e}")
                    return False
        logger.warning("超级鹰 click solve failed, waiting for manual interaction")

    # Manual fallback: just wait for user to click in the browser (headless=False)
    logger.info("Please click the captcha characters in the browser window...")
    screenshot_dir = Path("screenshots")
    screenshot_dir.mkdir(exist_ok=True)
    await page.screenshot(path=str(screenshot_dir / "click_captcha_manual.png"))
    try:
        await page.wait_for_selector(modal_sel, state="hidden", timeout=60_000)
        logger.info("点选验证码 modal closed (manual)")
        return True
    except Exception:
        logger.warning("Timed out waiting for captcha modal to close")
        return False
