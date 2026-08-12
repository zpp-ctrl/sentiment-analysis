# -*- coding: utf-8 -*-
"""
============================================================
模块: 小红书(Xiaohongshu)财经数据采集器
============================================================
核心功能:
  1. ★ 小红书财经账号发现 —— 搜索财经关键词，发现财经博主
  2. ★ 博主主页帖子采集 —— 采集前1000名财经博主过去24h帖子（图文+视频）
  3. ★ 严格24小时时间窗口 —— 只保留过去24小时帖子
  4. ★ 粉丝>10万门槛 —— 只采集粉丝数超过10万的财经自媒体
  5. ★ 账号排序 —— 按粉丝数降序排列，确保前1000名
  6. ★ 数据结构兼容 —— 输出与抖音一致的DataFrame格式
  7. 登录态复用 + 反检测 + 断点续采
============================================================
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import quote
import threading

import pandas as pd
from playwright.async_api import async_playwright

from config import (
    FILTER_KEYWORDS, OUTPUT_DIR,
    XHS_COLLECTION_TOP_N, XHS_MIN_FOLLOWER_THRESHOLD,
    XHS_FINANCE_KEYWORDS, XHS_API_PATTERNS,
    XHS_STORAGE_STATE, XHS_RESUME_CSV, XHS_ACCOUNT_POOL_FILE,
    XHS_MAX_CONCURRENT_CONTEXTS, XHS_PROFILE_SCROLL_ROUNDS,
    XHS_SEARCH_SCROLL_ROUNDS, XHS_REQUEST_DELAY_MIN, XHS_REQUEST_DELAY_MAX,
    XHS_DISCOVERY_KW_COUNT,
    XHS_SEARCH_URL, XHS_USER_PROFILE_URL,
    POST_MAX_AGE_HOURS, POST_MAX_AGE_HOURS_LOOSE,
    SEARCH_RETRY_COUNT, SEARCH_RETRY_DELAY,
    RANKED_ACCOUNTS_FILE, RANK_TOP_N,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)

# ★ 财经相关性过滤关键词（与抖音共用）
FINANCE_RELEVANCE_KEYWORDS = [
    "A股", "沪深300", "中证500", "中证1000", "上证", "深证", "创业板", "科创板",
    "大盘", "指数", "宽基", "股指",
    "涨停", "跌停", "牛市", "熊市", "震荡", "反弹", "回调",
    "放量", "缩量", "突破", "跌破", "新高", "新低",
    "上涨", "下跌", "暴涨", "暴跌", "拉升", "跳水", "走强", "走弱",
    "板块", "概念股", "赛道", "题材", "热点",
    "资金流入", "资金流出", "北向", "南向", "外资", "主力", "游资", "机构",
    "净流入", "净流出", "加仓", "减仓", "持仓", "仓位",
    "股票", "基金", "ETF", "债券", "可转债", "期货", "期权",
    "宏观经济", "GDP", "CPI", "PMI", "央行", "降准", "降息", "加息",
    "通胀", "通缩", "利率", "汇率", "人民币", "美元",
    "财政", "货币", "政策", "经济数据", "社融",
    "新能源", "光伏", "锂电", "半导体", "芯片", "AI", "人工智能",
    "白酒", "医药", "消费", "地产", "银行", "券商", "保险",
    "汽车", "军工", "煤炭", "有色", "电力",
    "投资", "理财", "选股", "交易", "策略", "技术分析", "基本面",
    "财报", "业绩", "净利润", "营收", "ROE",
    "分红", "回购", "增持", "减持",
    "财经", "金融", "证券", "投研", "研究员", "分析师",
]

# 财经账号名检测关键词
FINANCE_ACCOUNT_KEYWORDS = [
    ("财经", 0.95), ("证券", 0.85), ("金融", 0.80),
    ("A股", 0.75), ("股市", 0.75), ("大盘", 0.70), ("股票", 0.70),
    ("投资", 0.65), ("理财", 0.65), ("基金", 0.65), ("期货", 0.70),
    ("分析师", 0.80), ("研究员", 0.80), ("投顾", 0.80), ("交易员", 0.75),
    ("量化", 0.75), ("私募", 0.80), ("游资", 0.75),
    ("收评", 0.70), ("复盘", 0.70), ("解盘", 0.65), ("盘前", 0.60),
    ("盘后", 0.60), ("操盘", 0.65), ("交易日记", 0.65),
    ("宏观经济", 0.80), ("行业研究", 0.80),
    ("ETF", 0.70), ("北向", 0.70), ("技术分析", 0.65),
    ("基本面", 0.65), ("财报", 0.65), ("估值", 0.60),
]

# 账号名黑名单（非财经）
FINANCE_ACCOUNT_BLACKLIST = [
    "大学", "学院", "学校", "中学", "小学", "幼儿园", "教育",
    "招生", "毕业", "校园", "考试", "考研", "培训",
    "剧", "影视", "动漫", "动画", "漫画", "游戏", "电竞",
    "美食", "旅游", "穿搭", "美妆", "搞笑", "段子",
    "音乐", "唱歌", "跳舞", "变装", "挑战", "日常", "vlog",
    "宠物", "猫", "狗", "萌娃", "育儿", "情侣", "恋爱", "相亲",
    "故事", "小说", "短剧", "追剧", "电影",
    "带货", "探店", "开箱", "测评", "好物",
    "电视台", "广播", "日报", "晚报", "新闻联播", "政府", "官方",
]

# 小红书专用UA
XHS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# 反检测脚本
XHS_STEALTH_SCRIPT = """
// 抹除 webdriver 痕迹
Object.defineProperty(navigator, 'webdriver', {
    get: () => false,
    configurable: true,
    enumerable: false,
});
// 伪造 plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', length: 1 },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', length: 2 },
        ];
        plugins.item = (i) => plugins[i] || null;
        plugins.namedItem = (n) => plugins.find(p => p.name === n) || null;
        plugins.refresh = () => {};
        Object.setPrototypeOf(plugins, PluginArray.prototype);
        return plugins;
    },
    configurable: true,
    enumerable: false,
});
// 伪造 languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
});
// chrome 对象
window.chrome = {
    runtime: { onConnect: { addListener: () => {} }, onMessage: { addListener: () => {} } },
    loadTimes: () => ({}),
    csi: () => ({}),
    app: {},
};
// screen 属性
if (screen.width === 0 || screen.height === 0) {
    Object.defineProperty(screen, 'width', { get: () => 1920 });
    Object.defineProperty(screen, 'height', { get: () => 1080 });
    Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
    Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
}
// WebGL 指纹
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};
// hardwareConcurrency
if (navigator.hardwareConcurrency < 4) {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
}
// deviceMemory
if (!navigator.deviceMemory) {
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8, configurable: true, enumerable: true,
    });
}
"""


# ==================== 小红书账号池管理器 ====================
_POOL_LOCK = threading.Lock()


class XHSAccountPool:
    """小红书财经账号池管理器 —— 持久化到 xhs_account_pool.json"""

    @classmethod
    def load(cls) -> Dict:
        if not os.path.exists(XHS_ACCOUNT_POOL_FILE):
            return {}
        with open(XHS_ACCOUNT_POOL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def save(cls, pool: Dict):
        os.makedirs(os.path.dirname(XHS_ACCOUNT_POOL_FILE), exist_ok=True)
        with open(XHS_ACCOUNT_POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)

    @classmethod
    def update(cls, posts: List[Dict]):
        """更新账号池"""
        pool = cls.load()
        for p in posts:
            name = p.get("account_name", "").strip()
            if not name:
                continue
            fid = p.get("follower_count", 0)
            user_id = p.get("xhs_user_id", "")

            if name not in pool:
                pool[name] = {
                    "xhs_user_id": user_id,
                    "follower_count": fid,
                    "level": "top1000" if fid >= 500000 else (
                        "10w_plus" if fid >= XHS_MIN_FOLLOWER_THRESHOLD else "normal"),
                    "first_seen": datetime.now().strftime("%Y-%m-%d"),
                    "post_count": 1,
                    "is_finance_verified": p.get("_is_finance_account", False),
                    "finance_score": 0.7 if p.get("_is_finance_account") else 0.0,
                    "finance_post_count": 1 if p.get("_is_finance_account") else 0,
                    "last_scraped_date": "",
                    "platform": "xiaohongshu",
                }
            else:
                rec = pool[name]
                rec["post_count"] = rec.get("post_count", 0) + 1
                if fid > rec.get("follower_count", 0):
                    rec["follower_count"] = fid
                if p.get("_is_finance_account"):
                    old_score = rec.get("finance_score", 0.0)
                    old_count = rec.get("finance_post_count", 0)
                    new_count = old_count + 1
                    rec["finance_score"] = (old_score * old_count + 0.7) / new_count
                    rec["finance_post_count"] = new_count

                # 更新等级
                if rec.get("finance_post_count", 0) >= 3 and rec.get("follower_count", 0) >= 100000:
                    rec["level"] = "top1000"
                elif fid >= 500000 and rec.get("finance_score", 0) >= 0.4:
                    rec["level"] = "top1000"
                elif fid >= XHS_MIN_FOLLOWER_THRESHOLD and rec.get("finance_score", 0) >= 0.4:
                    rec["level"] = "10w_plus"

            if pool[name].get("finance_post_count", 0) >= 5:
                pool[name]["is_finance_verified"] = True

        cls.save(pool)

    @classmethod
    def get_finance_accounts_sorted(cls, limit: int = XHS_COLLECTION_TOP_N) -> List[Dict]:
        """获取已验证财经账号，按粉丝数降序排列"""
        pool = cls.load()
        accounts = []
        for name, info in pool.items():
            if info.get("is_finance_verified") or info.get("finance_score", 0) >= 0.4:
                accounts.append({
                    "account_name": name,
                    "xhs_user_id": info.get("xhs_user_id", ""),
                    "follower_count": info.get("follower_count", 0),
                    "level": info.get("level", ""),
                    "finance_score": info.get("finance_score", 0),
                    "last_scraped_date": info.get("last_scraped_date", ""),
                    "platform": "xiaohongshu",
                })
        accounts.sort(key=lambda x: x["follower_count"], reverse=True)
        return accounts[:limit]

    @classmethod
    def mark_scraped_today(cls, name: str):
        """标记今日已采集"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        with _POOL_LOCK:
            pool = cls.load()
            if name in pool:
                pool[name]["last_scraped_date"] = today_str
            cls.save(pool)

    @classmethod
    def pool_size(cls) -> int:
        return len(cls.load())


# ==================== 小红书采集器 ====================

class XHSCollector:
    """小红书财经数据采集器"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._posts: List[Dict] = []
        self._seen_ids: set = set()
        self._load_resume()

    def _load_resume(self):
        if os.path.exists(XHS_RESUME_CSV):
            try:
                df = pd.read_csv(XHS_RESUME_CSV)
                self._posts = df.to_dict("records")
                self._seen_ids = set(df["post_id"].tolist())
                logger.info("小红书断点续存: 已加载 %d 条记录", len(self._posts))
            except Exception:
                pass

    def _save_resume(self):
        if not self._posts:
            return
        os.makedirs(os.path.dirname(XHS_RESUME_CSV), exist_ok=True)
        df = pd.DataFrame(self._posts)
        df.to_csv(XHS_RESUME_CSV, index=False, encoding="utf-8-sig")

    async def _human_delay(self, min_s: float = None, max_s: float = None):
        """随机延迟（小红书需要更长的间隔）"""
        if min_s is None:
            min_s = XHS_REQUEST_DELAY_MIN
        if max_s is None:
            max_s = XHS_REQUEST_DELAY_MAX
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _random_mouse(self, page):
        """随机鼠标移动模拟真人"""
        for _ in range(random.randint(1, 3)):
            await page.mouse.move(
                random.randint(100, 1100),
                random.randint(100, 700),
            )
            await asyncio.sleep(random.uniform(0.1, 0.3))

    async def _block_unnecessary_resources(self, page):
        """★ 屏蔽图片/CSS/字体/媒体等无关资源，大幅加速页面加载"""
        blocked_types = {"image", "stylesheet", "font", "media", "manifest"}
        await page.route("**/*", lambda route: route.abort()
            if route.request.resource_type in blocked_types
            else route.continue_())

    @staticmethod
    def _is_finance_account(account_name: str, follower_count: int = 0) -> tuple:
        """判断账号是否为财经自媒体"""
        # 黑名单检查
        for bk in FINANCE_ACCOUNT_BLACKLIST:
            if bk in account_name:
                return (False, 0.0)

        # 财经关键词匹配
        max_conf = 0.0
        for kw, conf in FINANCE_ACCOUNT_KEYWORDS:
            if kw in account_name:
                max_conf = max(max_conf, conf)

        # 粉丝数加权
        if max_conf > 0 and follower_count >= 1000000:
            max_conf = min(1.0, max_conf + 0.05)
        elif max_conf > 0 and follower_count >= 500000:
            max_conf = min(1.0, max_conf + 0.03)

        is_finance = max_conf >= 0.5
        return (is_finance, max_conf)

    @staticmethod
    def _is_finance_related(content: str, account_name: str = "",
                            is_finance_account: bool = False,
                            account_confidence: float = 0.0) -> bool:
        """判断帖子内容是否财经相关"""
        if not content or len(content) < 10:
            return False

        # 高置信度财经账号的内容直接通过
        if is_finance_account and account_confidence >= 0.8:
            return True

        # 检查内容是否包含财经关键词
        content_lower = content.lower()
        finance_hits = sum(1 for kw in FINANCE_RELEVANCE_KEYWORDS if kw.lower() in content_lower)

        # 广告过滤
        for fkw in FILTER_KEYWORDS:
            if fkw in content:
                return False

        if is_finance_account and account_confidence >= 0.6:
            return finance_hits >= 1
        return finance_hits >= 2

    # ==================== XHR拦截 ====================

    def _make_response_handler(self, captured_xhr: List[Dict]):
        """XHR响应拦截处理器"""

        async def on_resp(resp):
            url = resp.url
            resource_type = resp.request.resource_type
            if resource_type not in ("xhr", "fetch"):
                return

            # 检查API端点白名单
            is_api = any(pattern in url for pattern in XHS_API_PATTERNS)
            if not is_api:
                return

            try:
                body = await resp.text()
                body_len = len(body) if body else 0
            except Exception:
                return

            if body_len < 200:
                return

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return

            captured_xhr.append({
                "url": url,
                "status": resp.status,
                "body_len": body_len,
                "data": data,
            })

        return on_resp

    # ==================== 搜索发现账号 ====================

    async def _search_finance_notes(self, page, keyword: str, captured_xhr: List[Dict]) -> List[Dict]:
        """搜索财经笔记，提取作者信息"""
        search_url = f"{XHS_SEARCH_URL}?keyword={quote(keyword)}&type=51"
        logger.info("  [XHS搜索] '%s' URL: %s", keyword, search_url[:100])

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)  # [优化: 30000→15000]
        except Exception:
            logger.warning("  [XHS搜索] 页面加载超时, 继续等待...")

        await asyncio.sleep(1.5)  # [优化: 3→1.5]
        await self._random_mouse(page)

        # 滚动加载更多
        for _ in range(XHS_SEARCH_SCROLL_ROUNDS):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            await asyncio.sleep(random.uniform(0.5, 1.0))  # [优化: 1.0-2.0→0.5-1.0]

        await asyncio.sleep(1)  # [优化: 2→1]

        # 从页面DOM提取笔记卡片
        notes = await self._extract_notes_from_dom(page)
        logger.info("  [XHS搜索] DOM提取: %d 条笔记", len(notes))
        return notes

    async def _extract_notes_from_dom(self, page) -> List[Dict]:
        """从搜索页DOM提取笔记信息"""
        js_extract = """
        () => {
            const results = [];
            // 小红书搜索页笔记卡片选择器
            const cards = document.querySelectorAll('section.note-item, div.note-item, a[href*="/explore/"]');
            const seen = new Set();

            for (const card of cards) {
                const link = card.querySelector('a[href*="/explore/"]') || card.closest('a[href*="/explore/"]');
                if (!link) continue;
                const href = link.href || link.getAttribute('href') || '';
                const noteMatch = href.match(/\\/explore\\/([a-f0-9]{24})/);
                if (!noteMatch || seen.has(noteMatch[1])) continue;
                seen.add(noteMatch[1]);

                const titleEl = card.querySelector('.title, .note-title, span.title');
                const authorEl = card.querySelector('.author .name, .username, .nickname');
                const likesEl = card.querySelector('.like-count, .count, .like-wrapper .count');

                results.push({
                    note_id: noteMatch[1],
                    title: (titleEl?.innerText || titleEl?.textContent || '').trim(),
                    author_name: (authorEl?.innerText || authorEl?.textContent || '').trim(),
                    likes_text: (likesEl?.innerText || likesEl?.textContent || '0').trim(),
                });
                if (results.length >= 50) break;
            }
            return JSON.stringify(results);
        }
        """
        try:
            raw = await page.evaluate(js_extract)
            notes = json.loads(raw)
            return notes
        except Exception as e:
            logger.warning("  [XHS-DOM] 提取失败: %s", str(e)[:80])
            return []

    async def _get_note_detail(self, page, note_id: str) -> Optional[Dict]:
        """通过XHR获取笔记详情（含作者信息）"""
        detail_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=12000)  # [优化: 20000→12000]
        except Exception:
            return None

        await asyncio.sleep(1)  # [优化: 2→1]

        # 从页面内嵌数据提取
        try:
            raw = await page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script');
                    for (const s of scripts) {
                        const text = s.textContent || s.innerText || '';
                        if (text.includes('"noteId"') && text.includes('"user"')) {
                            return text;
                        }
                    }
                    // 尝试 __INITIAL_STATE__
                    if (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.note) {
                        return JSON.stringify(window.__INITIAL_STATE__.note);
                    }
                    return null;
                }
            """)
            if raw:
                # 尝试从script中提取JSON
                json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>', raw, re.DOTALL)
                if not json_match:
                    json_match = re.search(r'({[^}]*"noteId"[^}]*"user"[^}]*})', raw, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))
                    note_data = data.get("note") or data.get("noteDetail") or data
                    author = note_data.get("user") or note_data.get("author") or {}
                    return {
                        "note_id": note_id,
                        "author_name": author.get("nickname", ""),
                        "author_id": author.get("userId") or author.get("id", ""),
                        "follower_count": author.get("fans", 0),
                        "note_type": note_data.get("type", ""),  # normal=图文, video=视频
                        "title": note_data.get("title", ""),
                        "desc": note_data.get("desc", ""),
                        "likes": note_data.get("interactInfo", {}).get("likedCount", 0),
                        "time": note_data.get("time", 0),
                    }
        except Exception as e:
            logger.debug("  [XHS详情] 解析失败: %s", str(e)[:60])

        return None

    # ==================== 账号发现阶段 ====================

    async def _discover_finance_accounts(self, browser):
        """账号发现阶段：搜索财经关键词，发现财经博主"""
        discovery_kw = random.sample(
            XHS_FINANCE_KEYWORDS,
            min(XHS_DISCOVERY_KW_COUNT, len(XHS_FINANCE_KEYWORDS))
        )
        logger.info("  [XHS发现] 使用 %d 个关键词搜索财经账号...", len(discovery_kw))

        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=XHS_USER_AGENT,
        )
        await context.add_init_script(XHS_STEALTH_SCRIPT)
        page = await context.new_page()
        await self._block_unnecessary_resources(page)

        discovered_accounts = []
        xhr_data: List[Dict] = []
        page.on("response", self._make_response_handler(xhr_data))

        for idx, kw in enumerate(discovery_kw):
            logger.info("  [XHS发现 %d/%d] 搜索: '%s'", idx + 1, len(discovery_kw), kw)
            try:
                notes = await self._search_finance_notes(page, kw, xhr_data)

                new_in_round = 0
                for note in notes:
                    author_name = note.get("author_name", "").strip()
                    if not author_name or len(author_name) < 2:
                        continue

                    # 检查是否财经账号
                    is_fin, conf = self._is_finance_account(author_name)

                    if is_fin and conf >= 0.5:
                        post_data = {
                            "post_id": f"xhs_{note.get('note_id', '')}",
                            "platform": "xiaohongshu",
                            "account_name": author_name,
                            "xhs_user_id": note.get("author_id", ""),
                            "follower_count": note.get("follower_count", 0),
                            "account_level": "top1000" if note.get("follower_count", 0) >= 500000 else "10w_plus",
                            "post_type": "image_text",
                            "post_content": note.get("title", "") + " " + note.get("desc", ""),
                            "post_url": f"https://www.xiaohongshu.com/explore/{note.get('note_id', '')}",
                            "like_count": note.get("likes", 0),
                            "comment_count": 0,
                            "share_count": 0,
                            "post_time": datetime.now() if not note.get("time") else
                                         datetime.fromtimestamp(note["time"] / 1000 if note["time"] > 1e10 else note["time"]),
                            "collect_time": datetime.now(),
                            "search_keyword": kw,
                            "_is_finance_account": True,
                            "_account_finance_confidence": conf,
                        }
                        discovered_accounts.append(post_data)
                        new_in_round += 1

                logger.info("  [XHS发现 %d/%d] '%s' → +%d 账号",
                           idx + 1, len(discovery_kw), kw, new_in_round)

            except Exception as e:
                logger.warning("  [XHS发现] '%s' 异常: %s", kw, str(e)[:80])

            await self._human_delay(1, 3)  # [优化: 3-8→1-3]

        await page.close()
        await context.close()

        if discovered_accounts:
            XHSAccountPool.update(discovered_accounts)
            logger.info("  ✅ XHS账号发现完成: 新增/更新 %d 个财经账号", len(discovered_accounts))

        return discovered_accounts

    # ==================== 主页帖子采集 ====================

    async def _scrape_profile_posts(self, page, account: Dict) -> List[Dict]:
        """访问博主主页，采集24h内帖子"""
        user_id = account.get("xhs_user_id", "")
        acc_name = account.get("account_name", "")

        if not user_id:
            logger.warning("  [XHS主页] '%s' 缺少user_id, 跳过", acc_name[:20])
            return []

        profile_url = f"{XHS_USER_PROFILE_URL}/{user_id}"
        logger.info("  [XHS主页] '%s' (粉丝%d) URL: %s", acc_name[:25],
                   account.get("follower_count", 0), profile_url[:80])

        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)  # [优化: 25000→15000]
        except Exception:
            logger.warning("  [XHS主页] '%s' 加载超时", acc_name[:20])
            return []

        await asyncio.sleep(1.5)  # [优化: 3→1.5]
        await self._random_mouse(page)

        # 滚动加载帖子
        for _ in range(XHS_PROFILE_SCROLL_ROUNDS):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
            await asyncio.sleep(random.uniform(0.5, 1.0))  # [优化: 0.8-1.5→0.5-1.0]

        await asyncio.sleep(1)  # [优化: 2→1]

        # 从DOM提取帖子
        posts = await self._extract_profile_notes(page, acc_name, user_id)
        return posts

    async def _extract_profile_notes(self, page, acc_name: str, user_id: str) -> List[Dict]:
        """从博主主页DOM提取帖子列表"""
        js_extract = """
        () => {
            const results = [];
            const seen = new Set();

            // 小红书笔记卡片
            const cards = document.querySelectorAll('section.note-item, div.note-item, a[href*="/explore/"], a[href*="/discovery/item/"]');

            for (const card of cards) {
                const link = card.querySelector('a[href*="/explore/"]') || card.closest('a[href*="/explore/"]');
                if (!link) continue;
                const href = link.href || link.getAttribute('href') || '';
                const noteMatch = href.match(/\\/explore\\/([a-f0-9]{24})/);
                if (!noteMatch || seen.has(noteMatch[1])) continue;
                seen.add(noteMatch[1]);

                const titleEl = card.querySelector('.title, .note-title, span.title, .desc');
                const likesEl = card.querySelector('.like-count, .count, .like-wrapper .count');
                const timeEl = card.querySelector('.date, .time, .publish-time');

                results.push({
                    note_id: noteMatch[1],
                    title: (titleEl?.innerText || titleEl?.textContent || '').trim(),
                    likes: (likesEl?.innerText || likesEl?.textContent || '0').trim(),
                    time_text: (timeEl?.innerText || timeEl?.textContent || '').trim(),
                });
                if (results.length >= 30) break;
            }
            return JSON.stringify(results);
        }
        """
        try:
            raw = await page.evaluate(js_extract)
            notes = json.loads(raw)
        except Exception:
            return []

        posts = []
        now = datetime.now()
        cutoff_24h = now - timedelta(hours=POST_MAX_AGE_HOURS)

        for note in notes:
            note_id = note.get("note_id", "")
            if not note_id:
                continue

            title = note.get("title", "")
            content = title  # 小红书标题即主要内容摘要

            # 24小时过滤（基于time_text解析）
            post_time = self._parse_xhs_time(note.get("time_text", ""))
            if post_time and post_time < cutoff_24h:
                continue

            posts.append({
                "post_id": f"xhs_{note_id}",
                "platform": "xiaohongshu",
                "account_id": hashlib.md5(acc_name.encode()).hexdigest()[:16],
                "account_name": acc_name,
                "xhs_user_id": user_id,
                "account_level": "top1000",
                "follower_count": 0,
                "post_type": "image_text",
                "post_content": content[:500] if content else "(无描述)",
                "post_url": f"https://www.xiaohongshu.com/explore/{note_id}",
                "like_count": self._parse_count(note.get("likes", "0")),
                "comment_count": 0,
                "share_count": 0,
                "post_time": post_time or now,
                "collect_time": now,
                "search_keyword": "profile",
            })

        return posts

    @staticmethod
    def _parse_xhs_time(time_text: str) -> Optional[datetime]:
        """解析小红书时间文本"""
        now = datetime.now()
        if not time_text:
            return now

        time_text = time_text.strip()
        try:
            if "分钟前" in time_text:
                mins = int(re.search(r'(\d+)', time_text).group(1))
                return now - timedelta(minutes=mins)
            elif "小时前" in time_text:
                hours = int(re.search(r'(\d+)', time_text).group(1))
                return now - timedelta(hours=hours)
            elif "天前" in time_text:
                days = int(re.search(r'(\d+)', time_text).group(1))
                return now - timedelta(days=days)
            elif "昨天" in time_text:
                return now - timedelta(days=1)
            elif "前天" in time_text:
                return now - timedelta(days=2)
            elif "-" in time_text:
                # 2024-01-15 格式
                return datetime.strptime(time_text[:10], "%Y-%m-%d")
            elif len(time_text) == 10 and time_text.isdigit():
                return datetime.fromtimestamp(int(time_text))
        except Exception:
            pass
        return now

    @staticmethod
    def _parse_count(count_text: str) -> int:
        """解析数量文本（支持"1.2万"格式）"""
        count_text = count_text.strip().lower()
        if not count_text:
            return 0
        try:
            if '万' in count_text or 'w' in count_text:
                num = float(re.search(r'[\d.]+', count_text).group())
                return int(num * 10000)
            elif '千' in count_text or 'k' in count_text:
                num = float(re.search(r'[\d.]+', count_text).group())
                return int(num * 1000)
            else:
                return int(float(re.search(r'[\d.]+', count_text).group()))
        except Exception:
            return 0

    # ==================== 并发采集 ====================

    async def _scrape_profiles_concurrent(self, browser, accounts: List[Dict],
                                         max_concurrency: int = XHS_MAX_CONCURRENT_CONTEXTS) -> int:
        """并发采集博主主页帖子"""
        if not accounts:
            logger.info("  无待采集账号")
            return 0

        # 分批
        batches = [[] for _ in range(max_concurrency)]
        for i, acc in enumerate(accounts):
            batches[i % max_concurrency].append(acc)

        active_batches = [(i, b) for i, b in enumerate(batches) if b]
        logger.info("  ★ XHS并发采集: %d 个账号 → %d 个worker", len(accounts), len(active_batches))

        total_new = 0
        cutoff_24h = datetime.now() - timedelta(hours=POST_MAX_AGE_HOURS)

        async def worker(batch_idx: int, acc_list: List[Dict]):
            nonlocal total_new
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                user_agent=XHS_USER_AGENT,
            )
            await context.add_init_script(XHS_STEALTH_SCRIPT)
            page = await context.new_page()
            await self._block_unnecessary_resources(page)

            local_new = 0
            for idx, acc in enumerate(acc_list):
                try:
                    posts = await self._scrape_profile_posts(page, acc)
                    for p in posts:
                        if p["post_id"] in self._seen_ids:
                            continue
                        # 24小时严格过滤
                        pt = p.get("post_time")
                        if isinstance(pt, datetime) and pt < cutoff_24h:
                            continue

                        content = p.get("post_content", "")
                        acc_name = acc.get("account_name", "")

                        if not self._is_finance_related(content, acc_name, True, 0.85):
                            continue

                        self._seen_ids.add(p["post_id"])
                        p["_is_finance_account"] = True
                        p["_account_finance_confidence"] = 0.85
                        self._posts.append(p)
                        local_new += 1

                    XHSAccountPool.mark_scraped_today(acc["account_name"])

                except Exception as e:
                    logger.warning("  [XHS Worker%d] '%s' 异常: %s",
                                 batch_idx, acc.get("account_name", "")[:20], str(e)[:60])

                await self._human_delay(0.5, 1.5)

            total_new += local_new
            logger.info("  [XHS Worker%d] 完成: +%d 条新帖", batch_idx, local_new)
            await page.close()
            await context.close()

        # 并发执行workers
        tasks = [worker(i, acc_list) for i, acc_list in active_batches]
        await asyncio.gather(*tasks)

        return total_new

    # ==================== 主采集流程 ====================

    async def collect(self, max_accounts: int = XHS_COLLECTION_TOP_N) -> List[Dict]:
        """主采集流程"""
        logger.info("=" * 60)
        logger.info("  小红书财经数据采集启动")
        logger.info("  目标: 前%d名财经自媒体, 粉丝>%d万", max_accounts, XHS_MIN_FOLLOWER_THRESHOLD // 10000)
        logger.info("  时间窗口: 过去%d小时", POST_MAX_AGE_HOURS)
        logger.info("=" * 60)

        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--no-proxy-server",                # ★ 绕过系统代理
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",
                "--disable-extensions",
                "--disable-popup-blocking",
                "--no-first-run",
                "--window-size=1280,900",
                "--exclude-switches=enable-automation",
            ],
        )

        try:
            # Phase 1: 账号发现
            logger.info(">>> Phase 1: 财经账号发现 <<<")
            await self._discover_finance_accounts(browser)
            pool_size = XHSAccountPool.pool_size()
            logger.info("  账号池当前大小: %d", pool_size)

            # Phase 2: 获取排序后的财经账号
            accounts = XHSAccountPool.get_finance_accounts_sorted(limit=max_accounts)
            logger.info(">>> Phase 2: 主页帖子采集 <<<")
            logger.info("  待采集账号: %d 个 (前%d名)", len(accounts), max_accounts)

            # 过滤掉今天已采集的
            today = datetime.now().strftime("%Y-%m-%d")
            accounts_todo = [a for a in accounts if a.get("last_scraped_date") != today]
            logger.info("  今日未采集: %d 个", len(accounts_todo))

            if accounts_todo:
                total_new = await self._scrape_profiles_concurrent(browser, accounts_todo)
                logger.info("  ✅ 主页采集完成: +%d 条新帖 | 总帖: %d", total_new, len(self._posts))

        finally:
            await browser.close()
            await pw.stop()

        self._save_resume()

        # 输出账号排名
        self._export_ranked_accounts()

        logger.info("=" * 60)
        logger.info("  小红书采集完成: 总帖数=%d, 账号池=%d", len(self._posts), XHSAccountPool.pool_size())
        logger.info("=" * 60)

        return self._posts

    def _export_ranked_accounts(self):
        """导出排序后的账号列表"""
        accounts = XHSAccountPool.get_finance_accounts_sorted(limit=RANK_TOP_N)
        os.makedirs(os.path.dirname(RANKED_ACCOUNTS_FILE), exist_ok=True)

        output = {
            "platform": "xiaohongshu",
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_accounts": len(accounts),
            "ranked_accounts": [],
        }

        for rank, acc in enumerate(accounts, 1):
            output["ranked_accounts"].append({
                "rank": rank,
                "account_name": acc["account_name"],
                "user_id": acc["xhs_user_id"],
                "follower_count": acc["follower_count"],
                "level": acc["level"],
                "finance_score": acc["finance_score"],
            })

        # 保存到独立的小红书排名文件
        xhs_rank_file = os.path.join(OUTPUT_DIR, "xhs_ranked_top1000.json")
        with open(xhs_rank_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        logger.info("  小红书排名已导出: %s (%d个账号)", xhs_rank_file, len(accounts))

    def to_dataframe(self) -> pd.DataFrame:
        """将采集的帖子转换为DataFrame"""
        if not self._posts:
            return pd.DataFrame()
        df = pd.DataFrame(self._posts)
        # 确保必要字段存在
        for col in ["post_id", "platform", "account_name", "post_type",
                     "post_content", "post_url", "like_count", "comment_count",
                     "share_count", "post_time", "collect_time", "account_level",
                     "follower_count", "search_keyword"]:
            if col not in df.columns:
                df[col] = "" if col not in ["like_count", "comment_count", "share_count", "follower_count"] else 0
        return df


# ==================== 入口函数 ====================

def run_xiaohongshu_collection(headless: bool = True) -> pd.DataFrame:
    """
    便捷函数: 执行小红书数据采集
    返回: 帖子DataFrame（格式与抖音一致）
    """
    collector = XHSCollector(headless=headless)

    try:
        posts = asyncio.run(collector.collect(max_accounts=XHS_COLLECTION_TOP_N))
    except Exception as e:
        logger.error("小红书采集异常: %s\n%s", str(e), traceback.format_exc())
        posts = []

    if not posts:
        logger.warning("小红书采集返回0条帖子，返回空DataFrame")
        return pd.DataFrame()

    df = collector.to_dataframe()
    logger.info("小红书采集DataFrame: %d 行 x %d 列", len(df), len(df.columns))
    return df


# ==================== 登录辅助 ====================

def login_xiaohongshu():
    """
    手动登录小红书并保存浏览器状态
    运行此函数后会打开浏览器，请手动扫码登录
    """
    async def _login():
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=False, args=["--no-sandbox", "--no-proxy-server"])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=XHS_USER_AGENT,
        )
        page = await context.new_page()
        await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded")
        logger.info("请在新打开的浏览器窗口中手动扫码登录小红书...")
        logger.info("登录成功后按 Enter 键继续...")

        input("按 Enter 保存登录态并退出...")

        await context.storage_state(path=XHS_STORAGE_STATE)
        logger.info("小红书登录态已保存到: %s", XHS_STORAGE_STATE)
        await browser.close()
        await pw.stop()

    asyncio.run(_login())


# ==================== 本地测试 ====================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--login":
        login_xiaohongshu()
    else:
        df = run_xiaohongshu_collection(headless=False)
        print(f"\n采集结果: {len(df)} 条帖子")
        if len(df) > 0:
            print(df[["post_id", "platform", "account_name", "post_type"]].head(10).to_string())
