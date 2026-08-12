# -*- coding: utf-8 -*-
"""
Douyin数据采集诊断脚本 - 单浏览器可见模式, 保存HTML源码用于分析
用法: python diagnose_douyin.py
"""
import asyncio, json, os, sys, time
from datetime import datetime

# 修复Windows编码
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from playwright.async_api import async_playwright

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "diagnose")
os.makedirs(OUTPUT_DIR, exist_ok=True)

STORAGE_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "douyin_storage_state.json")

FIXED_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
window.chrome = { runtime: {}, loadTimes: () => ({}), csi: () => ({}), app: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
"""

async def diagnose():
    has_login = os.path.exists(STORAGE_STATE)
    print(f"登录态: {'存在' if has_login else '不存在'}")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,  # 可见模式
        args=["--no-sandbox", "--window-size=1280,900", "--disable-blink-features=AutomationControlled"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=FIXED_UA,
        storage_state=STORAGE_STATE if has_login else None,
    )
    await ctx.add_init_script(STEALTH_SCRIPT)
    page = await ctx.new_page()

    ts = datetime.now().strftime("%H%M%S")

    # ====== 测试1: 抖音首页 ======
    print("\n[1/4] 加载抖音首页...")
    try:
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        title = await page.title()
        print(f"  标题: {title}")

        # 保存HTML
        html = await page.content()
        html_path = os.path.join(OUTPUT_DIR, f"{ts}_homepage.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html[:200000])
        print(f"  HTML已保存: {html_path} ({len(html)} bytes)")

        # 检查关键元素
        has_render = await page.evaluate("() => !!document.getElementById('RENDER_DATA')")
        has_video = await page.evaluate("() => document.querySelectorAll('a[href*=\"/video/\"]').length")
        print(f"  RENDER_DATA: {'有' if has_render else '无'}")
        print(f"  视频链接数: {has_video}")

        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"{ts}_homepage.png"), full_page=False)
        print(f"  截图已保存")
    except Exception as e:
        print(f"  错误: {e}")

    # ====== 测试2: 搜索页 ======
    print("\n[2/4] 测试搜索 'A股'...")
    captured = []

    async def on_resp(resp):
        if resp.request.resource_type in ("xhr", "fetch"):
            try:
                body = await resp.text()
                if len(body) > 200 and "aweme" in resp.url:
                    captured.append({"url": resp.url[:120], "status": resp.status, "len": len(body)})
            except:
                pass

    page.on("response", on_resp)

    try:
        await page.goto("https://www.douyin.com/search/A%E8%82%A1?type=general", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        # 滚动触发加载
        for i in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

        html = await page.content()
        html_path = os.path.join(OUTPUT_DIR, f"{ts}_search.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html[:200000])
        print(f"  HTML已保存: {html_path} ({len(html)} bytes)")

        # 检查搜索结果
        has_render = await page.evaluate("() => !!document.getElementById('RENDER_DATA')")
        video_count = await page.evaluate("() => document.querySelectorAll('a[href*=\"/video/\"]').length")
        print(f"  RENDER_DATA: {'有' if has_render else '无'}")
        print(f"  视频链接数: {video_count}")
        print(f"  捕获XHR数: {len(captured)}")
        for c in captured[:10]:
            print(f"    XHR: status={c['status']} len={c['len']} url={c['url']}")

        await page.screenshot(path=os.path.join(OUTPUT_DIR, f"{ts}_search.png"), full_page=False)
    except Exception as e:
        print(f"  错误: {e}")

    # ====== 测试3: 直接XHR请求 ======
    print("\n[3/4] 测试API直连...")
    try:
        # 先获取页面中的XHR来获取签名参数
        xhr_js = """
        async () => {
            try {
                const resp = await fetch('/aweme/v1/web/search/item/?aid=6383&keyword=A%E8%82%A1&count=10');
                const text = await resp.text();
                return {status: resp.status, len: text.length, ok: resp.ok};
            } catch(e) {
                return {error: e.message};
            }
        }
        """
        result = await page.evaluate(xhr_js)
        print(f"  API直连结果: {result}")
    except Exception as e:
        print(f"  错误: {e}")

    # ====== 测试4: 用户主页 ======
    print("\n[4/4] 测试用户主页...")
    # 用页面上找到的第一个sec_uid测试
    sec_uid = await page.evaluate("""
        () => {
            const rd = document.getElementById('RENDER_DATA');
            if (rd && rd.textContent) {
                const m = rd.textContent.match(/"sec_uid":"([^"]+)"/);
                return m ? m[1] : null;
            }
            return null;
        }
    """)
    if sec_uid:
        print(f"  找到sec_uid: {sec_uid[:40]}...")
        try:
            await page.goto(f"https://www.douyin.com/user/{sec_uid}", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            for i in range(5):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)

            html = await page.content()
            html_path = os.path.join(OUTPUT_DIR, f"{ts}_profile.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html[:200000])
            print(f"  HTML已保存: {html_path} ({len(html)} bytes)")

            video_count = await page.evaluate("() => document.querySelectorAll('a[href*=\"/video/\"]').length")
            has_render = await page.evaluate("() => !!document.getElementById('RENDER_DATA')")
            print(f"  RENDER_DATA: {'有' if has_render else '无'}")
            print(f"  视频链接数: {video_count}")

            await page.screenshot(path=os.path.join(OUTPUT_DIR, f"{ts}_profile.png"), full_page=False)
        except Exception as e:
            print(f"  错误: {e}")
    else:
        print("  未找到sec_uid, 跳过")

    # 收尾
    print(f"\n{'='*60}")
    print(f"诊断完成! 输出目录: {OUTPUT_DIR}")
    print(f"请查看HTML文件和截图了解Douyin实际返回内容")

    await ctx.close()
    await browser.close()
    await pw.stop()

if __name__ == "__main__":
    asyncio.run(diagnose())
