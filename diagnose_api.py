# -*- coding: utf-8 -*-
"""快速诊断: 检查搜索API返回的JSON结构"""
import asyncio, json, sys, os
try:
    sys.stdout.reconfigure(encoding='utf-8')
except:
    pass

from playwright.async_api import async_playwright
from urllib.parse import quote

STORAGE_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "douyin_storage_state.json")
FIXED_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

async def main():
    has_login = os.path.exists(STORAGE_STATE)
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--window-size=1280,900"])
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="zh-CN", timezone_id="Asia/Shanghai",
        user_agent=FIXED_UA,
        storage_state=STORAGE_STATE if has_login else None,
    )
    page = await ctx.new_page()

    # 拦截XHR
    captured = []
    async def on_resp(resp):
        if resp.request.resource_type in ("xhr", "fetch"):
            try:
                body = await resp.text()
                if len(body) > 500 and "aweme" in resp.url.lower():
                    captured.append({"url": resp.url[:150], "body": body[:3000]})
            except:
                pass
    page.on("response", on_resp)

    print("访问搜索页...")
    kw = "A股"
    await page.goto(f"https://www.douyin.com/search/{quote(kw)}?type=general",
                    wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)

    print(f"\n捕获 {len(captured)} 个相关XHR")
    for i, cap in enumerate(captured):
        print(f"\n{'='*60}")
        print(f"[{i}] URL: {cap['url'][:120]}")

        try:
            data = json.loads(cap['body'])
            # 分析结构
            def explore(obj, path="", depth=0):
                if depth > 6: return
                if isinstance(obj, dict):
                    keys = list(obj.keys())
                    if "aweme_id" in keys:
                        print(f"  >>> 找到帖子! path={path}")
                        print(f"  aweme_id={obj.get('aweme_id')}")
                        print(f"  desc={str(obj.get('desc',''))[:80]}")
                        author = obj.get('author', {})
                        print(f"  author={author.get('nickname','?') if isinstance(author, dict) else '?'}")
                        return
                    for k in keys[:8]:
                        v = obj[k]
                        if isinstance(v, list):
                            print(f"  {path}.{k}: list[{len(v)}]")
                            if len(v) > 0:
                                explore(v[0], f"{path}.{k}[0]", depth+1)
                        elif isinstance(v, dict):
                            print(f"  {path}.{k}: dict keys={list(v.keys())[:6]}")
                            if "aweme_id" in v:
                                explore(v, f"{path}.{k}", depth+1)
                        else:
                            val_str = str(v)[:60]
                            print(f"  {path}.{k}: {val_str}")
                elif isinstance(obj, list):
                    print(f"  {path}: list[{len(obj)}]")
                    if len(obj) > 0:
                        explore(obj[0], f"{path}[0]", depth+1)

            explore(data)

        except json.JSONDecodeError:
            print(f"  非JSON: {cap['body'][:200]}")

    await ctx.close()
    await browser.close()
    await pw.stop()

asyncio.run(main())
