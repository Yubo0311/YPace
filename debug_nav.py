"""诊断：找到包含'场地预约'文字的元素，打印其 tag/class/父级。"""
import asyncio
from playwright.async_api import async_playwright
from src.config_loader import load_credentials, load_config
from src.auth import login

async def main():
    creds = load_credentials()
    config = load_config()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await login(page, creds["username"], creds["password"])

        await page.goto("https://epe.pku.edu.cn/venue/home", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        result = await page.evaluate("""() => {
            // 找所有包含'场地预约'的元素
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const hits = [];
            let node;
            while (node = walker.nextNode()) {
                if (node.textContent.includes('场地预约')) {
                    const el = node.parentElement;
                    const p = el.parentElement;
                    hits.push({
                        tag: el.tagName,
                        text: el.innerText?.trim().slice(0, 60),
                        cls: el.className,
                        parentTag: p?.tagName,
                        parentCls: p?.className,
                        outerHTML: el.outerHTML.slice(0, 200)
                    });
                }
            }
            return hits;
        }""")

        print("\n=== 含'场地预约'的元素 ===")
        for r in result:
            print(f"\n  tag={r['tag']}  class='{r['cls']}'")
            print(f"  parent={r['parentTag']}  parentClass='{r['parentCls']}'")
            print(f"  outerHTML: {r['outerHTML']}")

        await page.screenshot(path="screenshots/home_debug.png")
        print("\n截图已保存到 screenshots/home_debug.png")
        await browser.close()

asyncio.run(main())
