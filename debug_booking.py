"""
诊断：打印预约页面的时间格颜色/class、日期控件结构。
"""
import asyncio
from playwright.async_api import async_playwright
from src.config_loader import load_credentials, load_config
from src.auth import login
from src.booker import _navigate_to_venue_listing, _wait_and_click_venue

VENUE_NAME = "五四体育中心-室外排球场"

async def main():
    creds = load_credentials()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        await login(page, creds["username"], creds["password"])
        await _navigate_to_venue_listing(page)
        ok = await _wait_and_click_venue(page, VENUE_NAME, timeout_sec=30)
        if not ok:
            print("未能进入场馆页面"); await browser.close(); return

        await page.wait_for_timeout(1500)
        print(f"URL: {page.url}\n")

        # ── 1. 日期控件 ───────────────────────────────────────────────────────
        date_els = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll(
                '[class*="date"], [class*="Date"], [class*="calendar"], [class*="picker"]'
            )).filter(el => el.innerText?.trim()).map(el => ({
                tag: el.tagName, text: el.innerText.trim().slice(0,60),
                cls: el.className, html: el.outerHTML.slice(0,300)
            }));
        }""")
        print("=== 日期控件 ===")
        for r in date_els[:10]:
            print(f"  [{r['tag']}] cls='{r['cls']}'\n    text='{r['text']}'\n    {r['html']}\n")

        # ── 2. ivu-table 的所有 tbody tr 行（时间段行） ───────────────────────
        rows = await page.evaluate("""() => {
            const rows = [];
            for (const tr of document.querySelectorAll('.ivu-table tbody tr, table tbody tr')) {
                const cells = Array.from(tr.querySelectorAll('td')).map(td => {
                    const st = window.getComputedStyle(td);
                    const divSt = td.querySelector('div') ?
                        window.getComputedStyle(td.querySelector('div')) : null;
                    return {
                        text: td.innerText?.trim().slice(0,20),
                        cls:  td.className,
                        divCls: td.querySelector('div')?.className || '',
                        bg:   st.backgroundColor,
                        divBg: divSt?.backgroundColor || ''
                    };
                });
                if (cells.length > 0) rows.push(cells);
            }
            return rows;
        }""")
        print("\n=== 时间格行 (tbody tr) ===")
        for row in rows[:15]:
            cells_str = " | ".join(
                f"'{c['text']}' bg={c['bg']} divBg={c['divBg']} cls={c['divCls']}"
                for c in row
            )
            print(f"  {cells_str}")

        # ── 3. 截图 ────────────────────────────────────────────────────────────
        await page.screenshot(path="screenshots/booking_page.png")
        print("\n截图: screenshots/booking_page.png")
        input("\n按 Enter 关闭...")
        await browser.close()

asyncio.run(main())
