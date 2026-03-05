"""
诊断时间列滚动：找到 '>' 按钮的真实位置和可滚动容器。
"""
import asyncio
from playwright.async_api import async_playwright
from src.config_loader import load_credentials
from src.auth import login
from src.booker import _navigate_to_venue_listing, _wait_and_click_venue, _select_date
from datetime import date, timedelta

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
        target = date.today() + timedelta(days=1)
        await _select_date(page, target)
        await page.wait_for_timeout(1000)

        result = await page.evaluate(r"""() => {
            // 1. 时间表头行的所有 cells 的 outerHTML
            let timeRow = null;
            for (const tr of document.querySelectorAll('tr')) {
                if (/\d{2}:\d{2}/.test(tr.innerText || '')) { timeRow = tr; break; }
            }
            const cells = timeRow
                ? Array.from(timeRow.querySelectorAll('td, th')).map((c, i) => ({
                    i, txt: (c.innerText || c.textContent || '').trim().slice(0, 30),
                    html: c.outerHTML.slice(0, 300)
                  }))
                : [];

            // 2. reserveBlock 的可滚动祖先链
            const block = document.querySelector('.reserveBlock');
            const ancestors = [];
            let el = block?.parentElement;
            while (el && el !== document.documentElement) {
                const st = window.getComputedStyle(el);
                ancestors.push({
                    tag: el.tagName,
                    cls: el.className.slice(0, 60),
                    overflowX: st.overflowX,
                    scrollLeft: el.scrollLeft,
                    scrollWidth: el.scrollWidth,
                    clientWidth: el.clientWidth,
                });
                el = el.parentElement;
            }

            // 3. 页面上所有含 '>' 文字或 next/right 类的可见元素
            const nextBtns = [];
            for (const el of document.querySelectorAll('*')) {
                if (!el.offsetParent) continue; // 跳过隐藏元素
                const txt = (el.innerText || el.textContent || '').trim();
                const cls = el.className || '';
                if (txt === '>' || txt === '›' || txt === '»' ||
                    /\bnext\b|\bright\b|\barrow\b/.test(cls)) {
                    nextBtns.push({
                        tag: el.tagName, cls: cls.slice(0, 60),
                        txt: txt.slice(0, 20),
                        html: el.outerHTML.slice(0, 200)
                    });
                }
            }

            return { cells, ancestors, nextBtns };
        }""")

        print("\n=== 时间表头 cells ===")
        for c in result['cells']:
            print(f"  [{c['i']}] '{c['txt']}'\n      {c['html']}")

        print("\n=== reserveBlock 的祖先链（含 overflow 信息）===")
        for a in result['ancestors']:
            print(f"  {a['tag']}.{a['cls']}  overflowX={a['overflowX']} "
                  f"scrollLeft={a['scrollLeft']} scrollWidth={a['scrollWidth']} clientWidth={a['clientWidth']}")

        print("\n=== 页面上的 '>' / next 元素 ===")
        for b in result['nextBtns']:
            print(f"  {b['tag']} cls='{b['cls']}' txt='{b['txt']}'\n    {b['html']}")

        await page.screenshot(path="screenshots/debug_slots.png")
        print("\n截图: screenshots/debug_slots.png")
        input("\nPress Enter 关闭...")
        await browser.close()

asyncio.run(main())
