# -*- coding: utf-8 -*-
"""
============================================================
模块1: 抖音财经数据采集 v6 (多线程并发 + 精准账号主页采集)
============================================================
核心功能:
  1. ★ 精准财经账号主页采集 —— 按粉丝量排序前1000名的财经博主
  2. ★ 多线程并发采集 —— 14个独立浏览器线程并行，高速完成
  3. ★ 严格24小时时间窗口 —— 只保留过去24小时帖子（视频+图文）
  4. ★ 视频/图文区分 —— post_type字段明确标识video/image_text
  5. ★ 博主全量采集 —— 发布视频的博主同时采集其图文帖子
  6. 采集目标: 前1000名财经自媒体 + 粉丝>10万的财经账号
  7. 断点续采 + 账号池持久化 + 登录态复用
  8. 反检测: navigator.webdriver抹除 + UA伪装 + 真人延迟模拟
  9. ★ v6: 30个发现关键词 + 更多API端点 + 严格24h过滤 + 排名导出
============================================================
"""

import asyncio, hashlib, json, logging, os, random, re, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import quote
import threading

import pandas as pd
from playwright.async_api import async_playwright

from config import (FILTER_KEYWORDS, OUTPUT_DIR, MIN_FOLLOWER_THRESHOLD,
                    COLLECTION_TOP_N, COLLECTION_SORT_BY, POST_MAX_AGE_HOURS,
                    POST_MAX_AGE_HOURS_LOOSE, SEARCH_RETRY_COUNT, SEARCH_RETRY_DELAY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)

# ★ 搜索关键词（100+ 覆盖多维度，每个词触发不同的搜索结果集）
FINANCE_KEYWORDS = [
    # === 指数名称（直接搜指数获取最精准的财经内容）===
    "上证指数今日", "沪深300分析", "中证500走势", "创业板行情",
    "上证指数", "沪深300", "中证500", "中证1000", "创业板", "科创板",
    # === 大盘行情 ===
    "A股复盘", "A股收评", "今日A股", "A股大盘", "股市行情",
    "A股", "股市", "大盘", "盘前分析", "盘后总结",
    # === 涨跌相关 ===
    "涨停", "跌停", "涨停板", "牛股", "妖股",
    # === 资金流向 ===
    "北向资金今日", "主力资金", "龙虎榜", "机构持仓",
    "北向资金", "资金流入", "资金流出", "南向资金",
    # === 板块热点 ===
    "板块轮动", "热点板块", "领涨板块", "今日热点",
    # === 行业板块 ===
    "券商板块", "银行板块", "白酒板块", "医药板块",
    "新能源板块", "半导体板块", "军工板块", "地产板块",
    "汽车板块", "光伏板块", "AI板块", "消费板块",
    # === 基金 ===
    "基金操作", "基金策略", "ETF投资", "基金定投",
    "基金", "ETF",
    # === 技术分析 ===
    "技术分析", "K线分析", "均线", "MACD", "成交量分析",
    "K线", "成交量",
    # === 基本面 ===
    "财报分析", "业绩预告", "估值分析", "基本面",
    # === 宏观 ===
    "宏观经济", "降准", "降息", "经济数据", "货币政策",
    "央行", "PMI", "CPI", "GDP",
    # === 投资策略 ===
    "投资策略", "选股方法", "仓位管理", "止盈止损",
    "股票投资", "价值投资", "短线交易", "波段操作",
    # === 财经资讯 ===
    "财经新闻", "财经解读", "市场解读", "财经",
    "证券", "金融", "投资",
    # === 时间维度（获取最新发布的内容）===
    "今日股市", "本周行情", "本月金股",
    # === 个股分析（容易匹配到财经账号）===
    "个股分析", "股票推荐", "研报解读",
]

# ★ 财经相关性过滤关键词 —— 帖子内容必须包含至少 1 个才算财经相关
FINANCE_RELEVANCE_KEYWORDS = [
    # 指数/大盘
    "A股", "沪深300", "中证500", "中证1000", "上证", "深证", "创业板", "科创板",
    "大盘", "指数", "宽基", "股指",
    # 行情术语（去掉单字词 "涨""跌" 避免误匹配）
    "涨停", "跌停", "牛市", "熊市", "震荡", "反弹", "回调",
    "放量", "缩量", "突破", "跌破", "新高", "新低", "阳线", "阴线",
    "开盘", "收盘", "成交量", "换手率", "PE", "估值", "股息",
    "上涨", "下跌", "暴涨", "暴跌", "拉升", "跳水", "走强", "走弱",
    # 板块
    "板块", "概念股", "赛道", "题材", "热点",
    # 资金
    "资金流入", "资金流出", "北向", "南向", "外资", "主力", "游资", "机构",
    "净流入", "净流出", "加仓", "减仓", "持仓", "仓位",
    # 金融品种
    "股票", "基金", "ETF", "债券", "可转债", "期货", "期权",
    # 经济宏观
    "宏观经济", "GDP", "CPI", "PMI", "央行", "降准", "降息", "加息",
    "通胀", "通缩", "利率", "汇率", "人民币", "美元",
    "财政", "货币", "政策", "经济数据", "社融",
    # 行业/个股
    "新能源", "光伏", "锂电", "半导体", "芯片", "AI", "人工智能",
    "白酒", "医药", "消费", "地产", "银行", "券商", "保险",
    "汽车", "军工", "煤炭", "有色", "电力",
    # 投资行为
    "投资", "理财", "选股", "交易", "策略", "技术分析", "基本面",
    "财报", "业绩", "净利润", "营收", "ROE",
    "分红", "回购", "增持", "减持",
    # 账号特征
    "财经", "金融", "证券", "投研", "研究员", "分析师",
]

# ★★★ 强财经信号词 —— 出现在内容中几乎必定是财经帖 ★★★
STRONG_FINANCE_INDICATORS = [
    # 指数精确名称（不会被误匹配）
    "上证指数", "沪深300", "中证500", "中证1000", "创业板指", "科创50",
    # 明确交易信号
    "涨停", "跌停", "涨停板", "跌停板",
    # 资金明确术语
    "北向资金", "南向资金", "龙虎榜", "主力净流入", "主力净流出",
    "机构持仓", "游资", "外资流入", "外资流出",
    # 明确技术分析
    "K线", "均线", "MACD", "KDJ", "布林带", "金叉", "死叉",
    "成交量放大", "换手率", "量比",
    # 明确市场行为
    "复盘", "收评", "盘前分析", "盘后总结", "操盘计划",
    "主升浪", "主跌浪",
    # 明确宏观
    "降准", "降息", "加息", "PMI数据", "CPI数据", "GDP增速",
    # 明确交易
    "打板", "半仓", "满仓", "空仓", "做T", "止损", "止盈",
    # 明确行情状态
    "牛市", "熊市", "普涨", "普跌",
]

# ★ 强噪声关键词 —— 帖子内容包含这些的，即使有财经词也大概率是噪声
NOISE_KEYWORDS = [
    # 广告/诈骗
    "加微信", "扫码领取", "免费荐股", "牛股推荐", "跟上操作",
    "内部票", "涨停预测", "加群", "私信", "点击链接",
    "限时特惠", "名额有限", "转发领取", "朋友圈集赞",
    "扣1", "扣波1", "评论666", "点赞领取",
    "稳赚", "保本", "高收益理财", "开户送礼", "佣金万",
    # 非财经日常
    "恋爱", "相亲", "男朋友", "女朋友", "出轨", "小三",
    "搞笑", "段子", "美食", "旅游", "穿搭", "美妆",
    "游戏", "王者荣耀", "吃鸡", "原神", "电竞",
    "唱歌", "跳舞", "变装", "挑战", "日常vlog",
    # 教育/校园（非财经内容标志）
    "毕业典礼", "开学", "录取", "军训", "校庆",
    "高考", "中考", "考研成绩", "论文答辩",
    "运动会", "社团", "学生会", "助学金",
    # 娱乐八卦
    "明星", "综艺", "偶像", "选秀", "八卦",
    "出轨", "离婚", "结婚", "恋情", "绯闻",
    # 体育
    "足球", "篮球", "世界杯", "奥运会", "金牌",
    "NBA", "CBA", "中超", "欧冠",
]

# ★★★ 财经账号名检测（Account-First 策略核心）★★★

# 账号名含这些关键词 → 高度可能是财经账号
FINANCE_ACCOUNT_KEYWORDS = [
    # 强标识（置信度 0.9）：账号名含这些几乎必定是财经号
    ("财经", 0.95), ("证券研究所", 0.95), ("投研", 0.90), ("基金经理", 0.90),
    ("证券", 0.85), ("金融", 0.80),
    # 中等标识（置信度 0.70）：较大概率是财经号
    ("A股", 0.75), ("股市", 0.75), ("大盘", 0.70), ("股票", 0.70),
    ("投资", 0.65), ("理财", 0.65), ("基金", 0.65), ("期货", 0.70),
    ("分析师", 0.80), ("研究员", 0.80), ("投顾", 0.80), ("交易员", 0.75),
    ("量化", 0.75), ("私募", 0.80), ("游资", 0.75), ("牛散", 0.70),
    # 内容标识（置信度 0.60）：账号名含这些词且粉丝多则可能是财经号
    ("收评", 0.70), ("复盘", 0.70), ("解盘", 0.65), ("盘前", 0.60),
    ("盘后", 0.60), ("操盘", 0.65), ("交易日记", 0.65),
    ("宏观经济", 0.80), ("行业研究", 0.80), ("公司研究", 0.75),
    ("ETF", 0.70), ("北向", 0.70), ("技术分析", 0.65),
    ("基本面", 0.65), ("财报", 0.65), ("估值", 0.60),
]

# 账号名含这些关键词 → 必定不是财经号（即使有财经词也是撞名）
FINANCE_ACCOUNT_BLACKLIST = [
    # 教育机构（"XX财经大学"不是财经自媒体）
    "大学", "学院", "学校", "中学", "小学", "幼儿园", "教育",
    "招生", "毕业", "校园", "考试", "考研", "培训",
    # 娱乐/生活类
    "剧", "影视", "动漫", "动画", "漫画", "游戏", "电竞",
    "美食", "旅游", "穿搭", "美妆", "搞笑", "段子",
    "音乐", "唱歌", "跳舞", "变装", "挑战", "日常", "vlog",
    "宠物", "猫", "狗", "萌娃", "育儿", "情侣", "恋爱", "相亲",
    "故事", "小说", "短剧", "追剧", "电影",
    # 带货/电商
    "带货", "探店", "开箱", "测评", "好物",
    # 政府/官方机构（非自媒体）
    "电视台", "广播", "日报", "晚报", "新闻联播", "政府", "官方",
    "公安", "检察", "法院", "税务", "海关",
]

STORAGE_STATE = os.path.join(OUTPUT_DIR, "douyin_storage_state.json")
RESUME_CSV = os.path.join(OUTPUT_DIR, "douyin_posts_resume.csv")
POOL_FILE = os.path.join(OUTPUT_DIR, "dy_account_pool.json")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "debug_screenshots")

# ★ v7: UA池随机轮换 —— 模拟不同Chrome版本的真实用户
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.140 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.92 Safari/537.36",
]
FIXED_UA = UA_POOL[0]  # 默认使用最新版本

# ★ v7: 随机取UA
def get_random_ua() -> str:
    return random.choice(UA_POOL)

# ★ v7: 随机viewport —— 模拟不同屏幕分辨率
VIEWPORT_POOL = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1680, "height": 1050},
    {"width": 1280, "height": 720},
]

def get_random_viewport() -> dict:
    return random.choice(VIEWPORT_POOL)

# ★ v7: sec-ch-ua 头部池 (匹配UA的Chrome版本)
SEC_CH_UA_POOL = [
    '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"',
    '"Google Chrome";v="130", "Chromium";v="130", "Not=A?Brand";v="24"',
    '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"',
]

def get_random_sec_ch_ua() -> str:
    return random.choice(SEC_CH_UA_POOL)

TIMEOUT_PAGE_GOTO = 12            # [优化: 30->20->12]
TIMEOUT_PAGE_EVAL = 6             # [优化: 15->10->6]
TIMEOUT_SEARCH_PER_KW = 30        # [优化: 90->60->30]

# ★ v7: 更强的反检测启动参数（模块级别，全局复用）
LAUNCH_ARGS = [
    "--no-sandbox",
    "--no-proxy-server",                # ★ 绕过系统代理(避免ERR_PROXY_CONNECTION_FAILED)
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
    "--disable-accelerated-2d-canvas",
    "--disable-gpu",
    "--disable-web-security",
    "--disable-features=VizDisplayCompositor",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-hang-monitor",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-sync",
    "--enable-features=NetworkServiceInProcess",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--no-first-run",
    "--password-store=basic",
    "--use-mock-keychain",
    "--window-size=1280,900",
    # ★ v7: 关键 —— 使用excludeSwitches移除自动化标识
    "--exclude-switches=enable-automation",
    "--exclude-switches=enable-logging",
]

# ==================== v6 优化采集配置 ====================
MAX_CONCURRENT_CONTEXTS = 20        # ★ 并发浏览器线程数 [优化: 6→14→20]
MAX_ACCOUNTS_TO_SCRAPE = 1200       # 单次采集最大账号数 (与COLLECTION_TOP_N对齐+缓冲)
MIN_FOLLOWER_ACCOUNTS = True        # ★ 包含粉丝>5万的财经自媒体(与config阈值一致)
PROFILE_PAGE_TIMEOUT_S = 10         # ★ 单账号主页加载超时(秒) [优化: 25→15→10]
PROFILE_SSR_WAIT_S = 1              # ★ 等待RENDER_DATA元素出现的额外时间(秒) [优化: 5→3→1]
PROFILE_SCROLL_ROUNDS = 3           # ★ 主页滚动次数 [优化: 15→8→3]
PROFILE_SCROLL_INTERVAL_S = 0.15    # ★ 滚动间隔(秒) [优化: 0.5→0.3→0.15]
PROFILE_XHR_WAIT_S = 0.3            # ★ 滚动完成后等待XHR回调的额外时间(秒) [优化: 3.0→1.5→0.3]
DISCOVERY_KW_COUNT = 50             # ★ 账号发现阶段关键词数（增加到50个，搜索是主要数据来源）
WORKER_DELAY_MIN_S = 0.05           # ★ 每个账号间最小延迟(秒) [优化: 3.0→0.5→0.05]
WORKER_DELAY_MAX_S = 0.3            # ★ 每个账号间最大延迟(秒) [优化: 8.0→2.0→0.3]
PROFILE_BATCH_REST_EVERY = 100      # ★ 每处理N个账号休息一次(防限流) [优化: 15→30→100]
PROFILE_BATCH_REST_MIN_S = 0.5      # ★ 批次休息最小秒数 [优化: 2→1→0.5]
PROFILE_BATCH_REST_MAX_S = 1.0      # ★ 批次休息最大秒数 [优化: 5→3→1]

# ★★★ 抖音帖子API端点白名单（只有这些URL的响应才提取帖子）★★★
POST_API_PATTERNS = [
    "/aweme/v1/web/aweme/favorite",   # ★ 用户主页帖子（当前有效）
    "/aweme/v1/web/aweme/post/",      # 用户主页帖子列表(v1)
    "/aweme/v2/web/aweme/post/",      # 用户主页帖子列表(v2)
    "/aweme/v3/web/aweme/post/",      # 用户主页帖子列表(v3)
    "/aweme/v1/web/search/item/",     # 搜索结果(v1)
    "/aweme/v2/web/search/item/",     # 搜索结果(v2)
    "/aweme/v1/web/general/search/single/",  # ★ v4: 新版搜索API
    "/aweme/v1/web/general/search/stream/",  # ★ v4: 搜索流API
    "/aweme/v1/web/aweme/detail/",    # 帖子详情
    "/aweme/v1/web/feed/",            # 推荐流
    "/aweme/v1/web/aweme/post",       # 主页帖子(无尾部斜杠)
    "/aweme/v1/web/aweme",            # 通用 aweme API
    # ★ v5: 新增端点
    "/aweme/v1/web/user/profile/",    # 用户主页信息API
    "/aweme/v1/web/aweme/favorite/",  # 收藏列表(尾部斜杠)
    "/aweme/v2/web/user/profile/",    # 用户主页v2
    "/aweme/v1/web/aweme/list/",      # 帖子列表
    "/aweme/v1/web/home/wap/",        # WAP首页
    # ★ v6: 新增端点（覆盖更多API路径）
    "/aweme/v1/web/tab/",            # 首页Tab流
    "/aweme/v1/web/aweme/related/",  # 相关帖子推荐
    "/aweme/v1/web/aweme/hot/",      # 热门帖子
    "/aweme/v1/web/discover/",       # 发现页
    "/aweme/v1/web/nearby/",         # 附近帖子
    "/aweme/v1/web/search/suggest/", # 搜索建议(可能含帖子)
    "/aweme/v1/web/aweme/collect/",  # 收藏夹
    "/aweme/v2/web/feed/",           # 推荐流v2
    "/aweme/v1/web/user/",           # 用户相关API(通用)
    "/aweme/v1/web/mix/",            # 混合内容流
    "/aweme/v1/web/aweme/music/",    # 音乐帖子列表
    "/aweme/v1/web/challenge/",      # 话题/挑战帖子
]
# 帖子必须包含的字段（缺少任一字段视为无效）
REQUIRED_POST_FIELDS = ["aweme_id", "desc"]


# ==================== 账号池 ====================
_POOL_LOCK = threading.Lock()  # 保护并发写入 dy_account_pool.json（跨线程安全）


class AccountPool:
    """财经账号池管理器 —— 持久化到 dy_account_pool.json"""

    @classmethod
    def load(cls) -> Dict:
        if not os.path.exists(POOL_FILE):
            return {}
        with open(POOL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def save(cls, pool: Dict):
        os.makedirs(os.path.dirname(POOL_FILE), exist_ok=True)
        with open(POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)

    @classmethod
    def update(cls, posts: List[Dict]):
        """更新账号池：每个帖子传入时累加统计"""
        pool = cls.load()
        for p in posts:
            name = p.get("account_name", "").strip()
            if not name:
                continue
            fid = p.get("follower_count", 0)
            is_finance_post = p.get("_is_finance_account", False)

            if name not in pool:
                pool[name] = {
                    "account_id": p.get("account_id", ""),
                    "sec_uid": p.get("sec_uid", ""),      # ★ 用于主页直链导航
                    "follower_count": fid,
                    "level": "top1000" if fid >= 500000 else (
                        "10w_plus" if fid >= MIN_FOLLOWER_THRESHOLD else "normal"),
                    "first_seen": datetime.now().strftime("%Y-%m-%d"),
                    "post_count": 1,
                    # ★ 新增字段
                    "is_finance_verified": is_finance_post,
                    "finance_score": 0.7 if is_finance_post else 0.0,
                    "finance_post_count": 1 if is_finance_post else 0,
                    "last_scraped_date": "",            # ★ 上次采集日期，避免同日重复
                }
            elif p.get("sec_uid") and not pool[name].get("sec_uid"):
                pool[name]["sec_uid"] = p["sec_uid"]
            else:
                rec = pool[name]
                rec["post_count"] = rec.get("post_count", 0) + 1
                if fid > rec.get("follower_count", 0):
                    rec["follower_count"] = fid
                if is_finance_post:
                    # 滚动平均更新置信度
                    old_score = rec.get("finance_score", 0.0)
                    old_count = rec.get("finance_post_count", 0)
                    new_count = old_count + 1
                    rec["finance_score"] = (old_score * old_count + 0.7) / new_count
                    rec["finance_post_count"] = new_count

                # ★ 修复: 只有财经账号才能获得对应等级
                if (rec.get("finance_post_count", 0) >= 3
                        and rec.get("follower_count", 0) >= 100000):
                    rec["level"] = "top1000"
                elif rec.get("is_finance_verified"):
                    rec["level"] = "top1000" if fid >= 500000 else "10w_plus"
                elif fid >= 500000 and rec.get("finance_score", 0) >= 0.4:
                    rec["level"] = "top1000"
                elif fid >= MIN_FOLLOWER_THRESHOLD and rec.get("finance_score", 0) >= 0.4:
                    rec["level"] = "10w_plus"

            # ★ 财经帖子数 ≥ 5 → 验证通过
            if pool[name].get("finance_post_count", 0) >= 5:
                pool[name]["is_finance_verified"] = True

        cls.save(pool)

    @classmethod
    def classify(cls, name: str, fid: int = 0) -> str:
        """查账号等级"""
        if fid >= 500000:
            return "top1000"
        if fid >= MIN_FOLLOWER_THRESHOLD:
            return "10w_plus"
        pool = cls.load()
        if name and name in pool:
            return pool[name].get("level", "10w_plus")
        return "10w_plus"

    @classmethod
    def lookup(cls, name: str) -> Optional[Dict]:
        """查单个账号信息"""
        pool = cls.load()
        return pool.get(name)

    @classmethod
    def is_verified_finance(cls, name: str) -> bool:
        """判断是否已验证的财经账号"""
        info = cls.lookup(name)
        if info is None:
            return False
        return info.get("is_finance_verified", False)

    @classmethod
    def get_finance_accounts(cls) -> List[str]:
        """获取所有已验证的财经账号名列表"""
        pool = cls.load()
        return [name for name, info in pool.items()
                if info.get("is_finance_verified")]

    @classmethod
    def get_unscraped_today(cls, limit: int = 200) -> List[tuple]:
        """
        获取今天尚未采集主页的财经账号（按粉丝量降序）。
        返回 [(name, sec_uid, followers), ...]
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        pool = cls.load()
        candidates = []
        for name, info in pool.items():
            sec = info.get("sec_uid", "")
            if not sec:
                continue
            # 今天已采集过 → 跳过
            if info.get("last_scraped_date") == today_str:
                continue
            # 必须是财经相关（已验证或有财经分）
            if not info.get("is_finance_verified") and info.get("finance_score", 0) < 0.3:
                continue
            followers = info.get("follower_count", 0)
            candidates.append((name, sec, followers))
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[:limit]

    @classmethod
    async def mark_scraped_today_async(cls, name: str):
        """标记某账号今日已采集（异步安全版，使用锁防止并发写入冲突）"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        with _POOL_LOCK:
            pool = cls.load()
            if name in pool:
                pool[name]["last_scraped_date"] = today_str
            cls.save(pool)

    @classmethod
    def mark_scraped_today(cls, name: str):
        """标记某账号今日已采集（同步版，保留向后兼容）"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        pool = cls.load()
        if name in pool:
            pool[name]["last_scraped_date"] = today_str
        cls.save(pool)


# ==================== 增强反检测初始化脚本 ====================

STEALTH_INIT_SCRIPT = """
// ================================================================
// ★ v7: 全面反检测脚本 —— 对抗抖音最新机器人检测技术
// 涵盖: webdriver抹除 / 指纹随机化 / 传感器API / CDP检测 / 字体枚举
// ================================================================

(function() {
    'use strict';

    // ===== [1] 彻底抹除 webdriver 痕迹（所有可能的访问路径）=====
    const webdriverProps = [
        {obj: navigator, prop: 'webdriver', value: false},
        {obj: navigator, prop: 'vendor', value: 'Google Inc.'},
        {obj: navigator, prop: 'vendorSub', value: ''},
        {obj: navigator, prop: 'productSub', value: '20030107'},
    ];
    webdriverProps.forEach(({obj, prop, value}) => {
        Object.defineProperty(obj, prop, {
            get: () => value,
            configurable: true,
        });
    });

    // ★ 删除 __proto__ 上的 webdriver（有些检测用 hasOwnProperty 绕过）
    delete Object.getPrototypeOf(navigator).webdriver;

    // ===== [2] 伪造 plugins 数组（真实Chrome的plugins列表）=====
    const FAKE_PLUGINS = [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', length: 1 },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', length: 2 },
    ];
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [...FAKE_PLUGINS];
            arr.item = (i) => arr[i] || null;
            arr.namedItem = (n) => arr.find(p => p.name === n) || null;
            arr.refresh = () => {};
            Object.setPrototypeOf(arr, PluginArray.prototype);
            return arr;
        },
        configurable: true,
    });

    // ===== [3] 伪造 languages + 平台信息 =====
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en-US', 'en'],
        configurable: true,
    });
    Object.defineProperty(navigator, 'language', {
        get: () => 'zh-CN',
        configurable: true,
    });
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32',
        configurable: true,
    });

    // ===== [4] 完整 chrome 对象 =====
    if (typeof window.chrome === 'undefined') {
        window.chrome = {};
    }
    window.chrome.runtime = window.chrome.runtime || {
        id: undefined,
        onConnect: { addListener: () => {}, removeListener: () => {} },
        onMessage: { addListener: () => {}, removeListener: () => {} },
        onInstalled: { addListener: () => {}, removeListener: () => {} },
        lastError: undefined,
        getManifest: () => ({}),
        getURL: (path) => 'chrome-extension://' + path,
        sendMessage: () => {},
        connect: () => ({ disconnect: () => {} }),
    };
    window.chrome.loadTimes = window.chrome.loadTimes || (() => ({
        requestTime: Date.now() / 1000 - 0.5,
        startLoadTime: Date.now() / 1000 - 0.4,
        commitLoadTime: Date.now() / 1000 - 0.3,
        finishDocumentLoadTime: Date.now() / 1000 - 0.1,
        finishLoadTime: Date.now() / 1000,
        firstPaintTime: Date.now() / 1000 - 0.2,
        firstPaintAfterLoadTime: Date.now() / 1000,
        navigationType: 'Other',
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: false,
        npnNegotiatedProtocol: 'http/1.1',
        wasAlternateProtocolAvailable: true,
        connectionInfo: 'h2',
    }));
    window.chrome.csi = window.chrome.csi || (() => ({
        startE: Date.now() - 2000,
        onloadT: Date.now() - 1000,
        pageT: 500 + Math.random() * 300,
        tran: 15,
    }));
    window.chrome.app = window.chrome.app || {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    };

    // ===== [5] 修复 permissions.query =====
    try {
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = function(parameters) {
            if (parameters.name === 'notifications') {
                return Promise.resolve({
                    state: Notification.permission || 'denied',
                    onchange: null,
                });
            }
            return origQuery.call(this, parameters);
        };
    } catch(e) {}

    // ===== [6] 修复 screen 属性 =====
    const screenProps = [
        ['width', () => window.innerWidth],
        ['height', () => window.innerHeight],
        ['availWidth', () => window.innerWidth],
        ['availHeight', () => window.innerHeight - 40],
        ['colorDepth', () => 24],
        ['pixelDepth', () => 24],
    ];
    screenProps.forEach(([prop, getter]) => {
        try {
            Object.defineProperty(screen, prop, { get: getter, configurable: true });
        } catch(e) {}
    });

    // ===== [7] WebGL 指纹伪造（Intel GPU是中文用户最常见的）=====
    try {
        const GPUS = [
            { vendor: 'Intel Inc.', renderer: 'Intel(R) UHD Graphics 630' },
            { vendor: 'Intel Inc.', renderer: 'Intel(R) Iris(R) Xe Graphics' },
            { vendor: 'Intel Inc.', renderer: 'Intel(R) HD Graphics 620' },
            { vendor: 'NVIDIA Corporation', renderer: 'NVIDIA GeForce GTX 1650' },
        ];
        const gpu = GPUS[Math.floor(Math.random() * GPUS.length)];

        const origGetParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return gpu.vendor;   // UNMASKED_VENDOR_WEBGL
            if (p === 37446) return gpu.renderer;  // UNMASKED_RENDERER_WEBGL
            return origGetParam.call(this, p);
        };

        // ★ 也覆盖 WebGL2RenderingContext
        if (typeof WebGL2RenderingContext !== 'undefined') {
            const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(p) {
                if (p === 37445) return gpu.vendor;
                if (p === 37446) return gpu.renderer;
                return origGetParam2.call(this, p);
            };
        }
    } catch(e) {}

    // ===== [8] Canvas 指纹加噪（更激进）=====
    try {
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        const origToBlob = HTMLCanvasElement.prototype.toBlob;
        const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

        HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
            try {
                const ctx = this.getContext('2d');
                if (ctx && this.width > 16 && this.height > 16) {
                    const imgData = ctx.getImageData(0, 0, 1, 1);
                    const noise = (Date.now() % 2);
                    imgData.data[0] = (imgData.data[0] + noise) % 256;
                    ctx.putImageData(imgData, 0, 0);
                }
            } catch(e) {}
            return origToDataURL.call(this, type, quality);
        };

        CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {
            const data = origGetImageData.call(this, x, y, w, h);
            // 微小噪点适用于所有getImageData调用
            if (data && data.data && data.data.length > 0 && (x === 0 && y === 0 && w <= 16 && h <= 16)) {
                data.data[0] = (data.data[0] + (Date.now() % 3)) % 256;
            }
            return data;
        };
    } catch(e) {}

    // ===== [9] AudioContext 指纹加噪 =====
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
            const origGetChannelData = AudioCtx.prototype.createAnalyser ?
                AudioCtx.prototype : null;
            // 覆盖 createDynamicsCompressor 产生的指纹
            const origCreateDyn = AudioCtx.prototype.createDynamicsCompressor;
            if (origCreateDyn) {
                AudioCtx.prototype.createDynamicsCompressor = function() {
                    const comp = origCreateDyn.call(this);
                    const origGetReduction = comp.reduction;
                    // reduction值微调
                    return comp;
                };
            }
        }
    } catch(e) {}

    // ===== [10] 伪造 deviceMemory =====
    try {
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => [8, 16, 32][Math.floor(Math.random() * 3)],
            configurable: true,
        });
    } catch(e) {}

    // ===== [11] 伪造 connection 信息 =====
    try {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false,
                type: 'wifi',
            }),
            configurable: true,
        });
    } catch(e) {}

    // ===== [12] 伪造 hardwareConcurrency =====
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => [4, 8, 12, 16][Math.floor(Math.random() * 4)],
            configurable: true,
        });
    } catch(e) {}

    // ===== [13] 去除 HeadlessChrome UA特征 =====
    const ua = navigator.userAgent;
    if (ua.includes('Headless') || ua.includes('headless')) {
        Object.defineProperty(navigator, 'userAgent', {
            get: () => ua.replace(/HeadlessChrome|headless/i, 'Chrome'),
            configurable: true,
        });
    }

    // ===== [14] 伪造 getBattery =====
    try {
        if (navigator.getBattery) {
            const origGetBattery = navigator.getBattery;
            navigator.getBattery = function() {
                return origGetBattery.call(this).catch(() => ({
                    charging: true,
                    chargingTime: 0,
                    dischargingTime: Infinity,
                    level: 0.7 + Math.random() * 0.3,
                    onchargingchange: null,
                    onchargingtimechange: null,
                    ondischargingtimechange: null,
                    onlevelchange: null,
                    addEventListener: () => {},
                    removeEventListener: () => {},
                }));
            };
        }
    } catch(e) {}

    // ===== [15] 伪造 touch 支持 (移动设备模拟，某些检测用) =====
    try {
        const maxTouchPoints = 0;  // 桌面端=0
        Object.defineProperty(navigator, 'maxTouchPoints', {
            get: () => maxTouchPoints,
            configurable: true,
        });
        Object.defineProperty(navigator, 'msMaxTouchPoints', {
            get: () => maxTouchPoints,
            configurable: true,
        });
    } catch(e) {}

    // ===== [16] 阻止 CDP (Chrome DevTools Protocol) 检测 =====
    // 某些网站通过检测 __commandLineAPI 或 debugger 来判断是否在自动化环境中
    try {
        Object.defineProperty(window, '__commandLineAPI', {
            get: () => undefined,
            configurable: true,
        });
    } catch(e) {}

    // ===== [17] 阻止 navigator.mediaDevices 空检测 =====
    try {
        if (!navigator.mediaDevices) {
            Object.defineProperty(navigator, 'mediaDevices', {
                get: () => ({
                    enumerateDevices: () => Promise.resolve([]),
                    getUserMedia: () => Promise.reject(new Error('NotAllowedError')),
                }),
                configurable: true,
            });
        }
    } catch(e) {}

    // ===== [18] 伪造 serviceWorker (headless常有差异) =====
    try {
        if (navigator.serviceWorker) {
            const origRegister = navigator.serviceWorker.register;
            navigator.serviceWorker.register = function() {
                return origRegister.apply(this, arguments).catch(() => null);
            };
        }
    } catch(e) {}

    // ===== [19] 时间相关API随机化(防时序指纹) =====
    const origPerfNow = performance.now.bind(performance);
    let timeNoise = 0;
    performance.now = function() {
        timeNoise += Math.random() * 0.2;
        return origPerfNow() + timeNoise;
    };

    // ===== [20] 内嵌iframe检测防御 =====
    // 在页面加载时，某些检测会创建一个隐藏iframe并检查其中navigator属性
    try {
        const origCreateElement = document.createElement.bind(document);
        document.createElement = function(tagName, options) {
            const el = origCreateElement(tagName, options);
            if (tagName.toLowerCase() === 'iframe') {
                const origSetAttr = el.setAttribute.bind(el);
                el.setAttribute = function(name, value) {
                    if (name === 'sandbox') {
                        value = (value || '') + ' allow-same-origin';
                    }
                    return origSetAttr(name, value);
                };
            }
            return el;
        };
    } catch(e) {}

    // ===== [21] ★★★ 关键: 在headless模式下模拟mousemove/mousedown事件 =====
    // 抖音检测是否有人类行为（headless下完全没有鼠标事件）
    if (typeof window.__stealth_mouse_initialized === 'undefined') {
        window.__stealth_mouse_initialized = true;
        // 在 document 上模拟一次初始化点击（表明有用户交互）
        setTimeout(() => {
            try {
                document.dispatchEvent(new MouseEvent('mousemove', {
                    clientX: Math.random() * 800 + 100,
                    clientY: Math.random() * 500 + 100,
                    bubbles: true,
                }));
            } catch(e) {}
        }, 500 + Math.random() * 1000);
    }

    console.debug('[stealth] v7 反检测脚本已注入');
})();
"""


# ==================== 核心采集器 ====================

class DouyinCollector:
    def __init__(self, headless: bool = False, diagnose: bool = False):
        """
        Args:
            headless: 是否使用无头模式。
            diagnose: 诊断模式，输出所有XHR详情用于排查0条问题。
        """
        self.headless = headless
        self.diagnose = diagnose
        self._posts: List[Dict] = []
        self._seen_ids: set = set()
        self._load_resume()

    def _load_resume(self):
        if os.path.exists(RESUME_CSV):
            try:
                df = pd.read_csv(RESUME_CSV)
                self._posts = df.to_dict("records")
                self._seen_ids = set(df["post_id"].tolist())
                logger.info("断点续存: 已加载 %d 条记录", len(self._posts))
            except Exception:
                pass

    def _save_resume(self):
        if not self._posts:
            return
        os.makedirs(os.path.dirname(RESUME_CSV), exist_ok=True)
        df = pd.DataFrame(self._posts)
        df.to_csv(RESUME_CSV, index=False, encoding="utf-8-sig")

    async def _human_delay(self, min_s: float = 0.1, max_s: float = 0.5):
        """随机延迟 [优化: 默认0.3-1.5 → 0.1-0.5]"""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _human_scroll(self, page, times: int = None):
        """真人式分段滚动（预热用，非数据加载）"""
        if times is None:
            times = random.randint(2, 5)
        for _ in range(times):
            dy = random.randint(200, 500)
            await page.evaluate(f"window.scrollBy(0, {dy})")
            await self._human_delay(0.3, 0.8)

    async def _humanlike_scroll(self, page, rounds: int = 8):
        """★ 快速滚动 —— 极速模式，只滚到底部触发XHR"""
        for rnd in range(rounds):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(0.1, 0.3))  # [优化: 0.4-1.5 → 0.1-0.3]

    async def _scroll_to_load(self, page, rounds: int = 15, wait_between: float = 1.5):
        """
        ★ 可靠的懒加载滚动：每次滚到底部 + 等待网络请求触发
        抖音在页面滚到底部时才发 API 请求加载更多内容
        """
        loaded = 0
        for rnd in range(rounds):
            # 滚到页面底部（触发懒加载）
            prev_height = await page.evaluate("document.body.scrollHeight")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # 模拟鼠标滚轮（更真实）
            await page.mouse.wheel(0, random.randint(300, 600))
            # 等待新的 API 请求发出并返回
            await asyncio.sleep(wait_between)
            # 检查页面是否变长（有新内容加载）
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height > prev_height:
                loaded += 1
        logger.debug("  [滚动] %d/%d 次有新内容加载", loaded, rounds)

    async def _random_mouse(self, page):
        """随机鼠标移动"""
        for _ in range(random.randint(1, 3)):
            await page.mouse.move(
                random.randint(100, 1100),
                random.randint(100, 700),
            )
            await self._human_delay(0.05, 0.2)

    async def _block_unnecessary_resources(self, page):
        """★ 屏蔽图片/CSS/字体/媒体等无关资源，大幅加速页面加载"""
        blocked_types = {"image", "stylesheet", "font", "media", "manifest"}
        await page.route("**/*", lambda route: route.abort()
            if route.request.resource_type in blocked_types
            else route.continue_())

    async def _save_debug_screenshot(self, page, label: str):
        """保存调试截图"""
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S")
        path = os.path.join(SCREENSHOT_DIR, f"{ts}_{label}.png")
        try:
            await page.screenshot(path=path, full_page=False)
            logger.info("  [截图] 已保存: %s", path)
        except Exception as e:
            logger.debug("  [截图] 失败: %s", str(e)[:60])

    async def _save_debug_html(self, page, label: str):
        """★诊断: 保存页面HTML源码用于排查提取失败原因"""
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S")
        path = os.path.join(SCREENSHOT_DIR, f"{ts}_{label}.html")
        try:
            html = await page.content()
            # 只保存前500KB（足够诊断SSR数据结构）
            with open(path, "w", encoding="utf-8") as f:
                f.write(html[:500000])
            logger.info("  [HTML] 已保存: %s (%d字节)", path, len(html))
        except Exception as e:
            logger.debug("  [HTML] 失败: %s", str(e)[:60])

    # ==================== 响应拦截(v3: URL白名单 + 严格匹配) ====================

    # 记录发现的新API端点(诊断用)
    _discovered_endpoints: set = set()

    @staticmethod
    def _clean_json_body(body: str) -> str:
        """
        ★ v5: 清洗响应体中的chunked编码前缀等非JSON内容
        抖音部分API返回 Transfer-Encoding: chunked, 导致body前有hex长度标记
        """
        if not body:
            return body
        # 去除开头的chunked大小标记 (如 "e6a0\r\n")
        import re as _re
        body = _re.sub(r'^[0-9a-fA-F]+\r?\n', '', body.strip())
        # 如果仍然不以 { 或 [ 开头，尝试找到JSON起始位置
        if body and body[0] not in '{[':
            for ch in ['{', '[']:
                idx = body.find(ch)
                if idx > 0:
                    body = body[idx:]
                    break
        return body

    def _make_response_handler(self, captured_xhr: List[Dict]):
        """
        v3: 只从已知帖子API端点提取数据。
        - URL 必须在 POST_API_PATTERNS 白名单中
        - 响应必须包含 aweme_id/desc 等帖子特征字段
        - ★ 移除危险的降级逻辑(不再取任意列表)
        """

        async def on_resp(resp):
            url = resp.url
            resource_type = resp.request.resource_type

            # 只看 xhr / fetch
            if resource_type not in ("xhr", "fetch"):
                return

            # ★★★ v3 关键: URL白名单过滤 ★★★
            is_post_api = any(pattern in url for pattern in POST_API_PATTERNS)
            if not is_post_api and not self.diagnose:
                # 记录未匹配端点（诊断用）
                simplified = url.split("?")[0]
                if "aweme" in simplified or "douyin" in simplified:
                    if simplified not in self._discovered_endpoints:
                        self._discovered_endpoints.add(simplified)
                        logger.debug("  [api-discover] 新端点(未使用): %s", simplified[:120])
                return

            # 诊断模式: 记录所有XHR（不限URL）
            if self.diagnose and not is_post_api:
                logger.info("  [diagnose-XHR] status=%d url=%.120s", resp.status, url[:120])

            try:
                content_type = resp.headers.get("content-type", "")
                body = await resp.text()
                body_len = len(body) if body else 0
            except Exception:
                return

            if body_len < 200:
                return

            # ★ v5: 清洗chunked编码前缀
            clean_body = self._clean_json_body(body)

            # 尝试 JSON 解析
            try:
                data = json.loads(clean_body)
            except json.JSONDecodeError:
                logger.debug("  [xhr-nonjson] url=%.100s", url[:100])
                return

            # ★ v4: 如果data是list（某些新版API直接返回数组）
            items = None
            if isinstance(data, list):
                # 检查是否直接是帖子列表
                if len(data) > 0 and isinstance(data[0], dict):
                    sample_keys = set(data[0].keys())
                    post_indicators = {"aweme_id", "desc", "author", "create_time", "statistics", "video", "images"}
                    if sample_keys & post_indicators:
                        items = data
                        items_path = "root_array"
                        logger.debug("  [xhr-array] 直接从数组提取 %d 个帖子", len(items))
                if items is None:
                    logger.debug("  [xhr-list] url=%.100s len=%d (非帖子列表)", url[:100], len(data))
                    return
            elif not isinstance(data, dict):
                return
            else:
                top_keys = list(data.keys())[:15]

                # ★ v4: 先检测 search/single API 的 aweme_info 包裹格式
                if "data" in data and isinstance(data["data"], list):
                    data_list = data["data"]
                    if len(data_list) > 0 and isinstance(data_list[0], dict):
                        # 检查是否是 {type, aweme_info} 格式
                        if "aweme_info" in data_list[0]:
                            items = [item["aweme_info"] for item in data_list
                                     if isinstance(item.get("aweme_info"), dict)]
                            items_path = "data[].aweme_info"
                            if items:
                                logger.debug("  [xhr-search] 解包 aweme_info: %d 条", len(items))

                # ★★★ 查找帖子列表（严格匹配） ★★★
                if items is None:
                    items = None
                    items_path = ""

                    def _find_lists(obj, path=""):
                        nonlocal items, items_path
                        if isinstance(obj, dict):
                            # ★ v4: 检测单帖对象（aweme_id + desc同层）
                            if "aweme_id" in obj and "desc" in obj and len(obj) >= 3:
                                pass  # 单帖不收集，等列表匹配
                            for k, v in obj.items():
                                _find_lists(v, f"{path}.{k}" if path else k)
                        elif isinstance(obj, list) and len(obj) > 0:
                            if isinstance(obj[0], dict):
                                sample_keys = set(obj[0].keys())
                                post_indicators = {
                                    "aweme_id", "desc", "author", "create_time",
                                    "statistics", "video", "images",
                                }
                                if sample_keys & post_indicators:
                                    if items is None or len(obj) > len(items):
                                        items = obj
                                        items_path = path

                    _find_lists(data)

                if not items:
                    logger.debug(
                        "  [xhr-skip] 端点匹配但无帖子列表: keys=%s url=%.100s",
                        top_keys, url[:100],
                    )
                    return

            record = {
                "url": url,
                "resource_type": resource_type,
                "status": resp.status,
                "content_type": content_type[:80],
                "body_len": body_len,
                "top_keys": top_keys,
                "items": items,
                "items_path": items_path,
                "data": data,
            }
            captured_xhr.append(record)

            logger.info(
                "  [xhr-match✓] status=%d items=%d path=%s keys=%s url=%.100s",
                resp.status, len(items), items_path,
                (list(items[0].keys())[:8] if isinstance(items[0], dict) else "?"),
                url[:100],
            )

        return on_resp

    def _make_broad_response_handler(self, captured_xhr: List[Dict]):
        """
        ★ v4: 宽泛XHR捕获 —— 用于搜索页面，不限API白名单。
        捕获所有包含帖子数据的XHR/fetch响应，从中递归搜索 aweme_id。
        """
        async def on_resp(resp):
            url = resp.url
            resource_type = resp.request.resource_type
            if resource_type not in ("xhr", "fetch"):
                return

            try:
                body = await resp.text()
                body_len = len(body) if body else 0
            except Exception:
                return

            if body_len < 200:
                return

            # ★ v5: 清洗chunked编码前缀
            clean_body = self._clean_json_body(body)

            try:
                data = json.loads(clean_body)
            except json.JSONDecodeError:
                return

            # 处理list
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    sample_keys = set(data[0].keys())
                    if sample_keys & {"aweme_id", "desc", "create_time"}:
                        captured_xhr.append({
                            "url": url, "items": data, "top_keys": list(sample_keys)[:10],
                            "data": None, "items_path": "root_array"
                        })
                return

            if not isinstance(data, dict):
                return

            top_keys = list(data.keys())[:15]

            # ★ v4: 先检测 search/single API aweme_info 包裹格式
            items = None
            items_path = ""
            if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
                first = data["data"][0]
                if isinstance(first, dict) and "aweme_info" in first:
                    items = [item["aweme_info"] for item in data["data"]
                             if isinstance(item.get("aweme_info"), dict)]
                    items_path = "data[].aweme_info"

            # 递归搜索帖子列表
            if items is None:
                def _find(data_obj, path=""):
                    nonlocal items, items_path
                    if isinstance(data_obj, dict):
                        for k, v in data_obj.items():
                            _find(v, f"{path}.{k}" if path else k)
                    elif isinstance(data_obj, list) and len(data_obj) > 0:
                        if isinstance(data_obj[0], dict):
                            s = set(data_obj[0].keys())
                            if s & {"aweme_id", "desc", "create_time"}:
                                if items is None or len(data_obj) > len(items):
                                    items = data_obj
                                    items_path = path

                _find(data)

            if items:
                captured_xhr.append({
                    "url": url, "items": items, "top_keys": top_keys,
                    "items_path": items_path, "data": data,
                })
            else:
                # 保存原始数据供诊断
                captured_xhr.append({
                    "url": url, "items": None, "top_keys": top_keys,
                    "items_path": "", "data": data,
                })

        return on_resp

    # ==================== 搜索策略(v4: 宽泛XHR + URL直链) ====================

    async def _search_via_input(
        self, page, keyword: str, captured_xhr: List[Dict],
    ) -> List[Dict]:
        """
        ★ v4.1: 完全自建XHR捕获，不依赖外部captured_xhr。
        首页搜索框输入 → 回车 → 自建handler捕获 → 提取
        ★ v7: 增加类人行为模拟 + 更详细诊断
        """
        # ★ v4.1: 创建页面专属的XHR捕获列表
        _local_xhr: List[Dict] = []
        page.on("response", self._make_broad_response_handler(_local_xhr))

        logger.info("  [搜索-步骤1/4] 加载首页...")
        try:
            await page.goto("https://www.douyin.com", wait_until="domcontentloaded",
                           timeout=TIMEOUT_PAGE_GOTO * 1000)
        except Exception:
            logger.warning("  [搜索-步骤1/4] 首页加载超时(可忽略)")

        await asyncio.sleep(random.uniform(1, 2))  # [优化: 2-4→1-2]
        page_title = await page.title()
        logger.info("  [搜索-步骤1/4] 首页标题: %s", page_title[:60])

        # ★ v7: 检测是否遇到验证页面
        if any(kw in page_title for kw in ["验证", "captcha", "滑块", "安全"]):
            logger.error("  [搜索] ❌ 触发验证码/安全页面！请重新登录")
            await self._save_debug_screenshot(page, f"captcha_detected_{keyword}")
            return []

        # 查找并点击搜索框
        logger.info("  [搜索-步骤2/4] 查找搜索框...")
        search_selectors = [
            'input[placeholder*="搜索"]',
            'input[type="search"]',
        ]

        search_clicked = False
        for sel in search_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2000):
                    await loc.click(timeout=3000)
                    await self._human_delay(0.3, 0.8)
                    search_clicked = True
                    break
            except Exception:
                continue

        if not search_clicked:
            logger.warning("  [搜索] ❌ 搜索框未找到 → URL直链")
            return await self._search_via_url(keyword, [])

        # ★ 输入关键词 + 回车
        logger.info("  [搜索-步骤3/4] 输入关键词 '%s'...", keyword)
        try:
            input_sel = page.locator(
                'input[placeholder*="搜索"], input[type="search"], input[type="text"]'
            ).first
            if await input_sel.is_visible(timeout=2000):
                await input_sel.fill("")
                await asyncio.sleep(0.2)
                await input_sel.type(keyword, delay=random.randint(30, 80))
                await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
            else:
                return await self._search_via_url(keyword, [])
        except Exception as e:
            logger.warning("  [搜索] ❌ 输入失败: %s → URL直链", str(e)[:60])
            return await self._search_via_url(keyword, [])

        # ★ 等待搜索页加载 + 滚动触发更多XHR
        await asyncio.sleep(random.uniform(1.0, 2.0))  # [优化: 2-3→1-2]
        await self._humanlike_scroll(page, rounds=3)    # [优化: 5→3]
        await asyncio.sleep(random.uniform(0.5, 1.5))   # [优化: 1-2→0.5-1.5]

        # ★ 从自建的_local_xhr中提取
        logger.info("  [搜索-步骤4/4] 提取搜索结果 (XHR=%d个)...", len(_local_xhr))
        posts = self._extract_posts_from_xhr_broad(_local_xhr, keyword)
        if posts:
            valid = [p for p in posts if self._validate_post(p)]
            logger.info("  [搜索-结果] ✅ %d条原始 → %d条有效", len(posts), len(valid))
            if valid:
                # 回填到captured_xhr供调用方诊断
                captured_xhr.extend(_local_xhr)
                return valid

        # DOM兜底
        dom_posts = await self._extract_from_dom(page, keyword)
        if dom_posts:
            logger.info("  [搜索-结果] ✅ DOM兜底: %d条", len(dom_posts))
            captured_xhr.extend(_local_xhr)
            return dom_posts

        # ★ v7: 增强诊断
        logger.warning("  [搜索-诊断] ❌ '%s': 0条! XHR=%d个 URL=%s",
                       keyword, len(_local_xhr), page.url[:100])
        # 输出前5个XHR的详细信息
        meaningful = 0
        for i, cap in enumerate(_local_xhr[:10]):
            items_count = len(cap.get("items", [])) if cap.get("items") else 0
            has_data = "data" in cap and cap["data"] is not None
            if items_count > 0 or has_data:
                meaningful += 1
                logger.warning("    XHR[%d]: items=%d has_data=%s keys=%s url=%.100s",
                              i, items_count, has_data,
                              cap.get("top_keys", [])[:6], cap.get("url", "")[:100])
        if meaningful == 0:
            logger.error("  [搜索-致命] 所有%d个XHR响应均无帖子数据！", len(_local_xhr))
            logger.error("  可能原因: 1)Cookie过期需重新登录 2)触发验证码 3)API端点已变更")
            logger.error("  建议: python module1_account_collector.py --login 重新登录")
        captured_xhr.extend(_local_xhr)
        await self._save_debug_screenshot(page, f"nodata_search_{keyword}")
        await self._save_debug_html(page, f"nodata_search_{keyword}")

    async def _search_via_url(
        self, keyword: str, captured_xhr: List[Dict],
    ) -> List[Dict]:
        """
        ★ v5: 直接URL搜索 —— 访问 search/{keyword} → SSR提取 → 滚动XHR → DOM兜底
        多层提取策略确保不遗漏数据:
          1. SSR内嵌数据(RENDER_DATA / __INITIAL_STATE__)
          2. XHR拦截(宽泛模式)
          3. DOM提取兜底
        """
        logger.info("  [URL搜索] '%s' 启动...", keyword)
        has_login = os.path.exists(STORAGE_STATE)
        posts = []

        for attempt in range(1, SEARCH_RETRY_COUNT + 1):
            if attempt > 1:
                delay = SEARCH_RETRY_DELAY * attempt
                logger.info("  [URL搜索] '%s' 第%d次重试 (等待%ds)...", keyword, attempt, delay)
                await asyncio.sleep(delay)

            try:
                pw2 = await async_playwright().start()
                browser2 = await pw2.chromium.launch(
                    headless=self.headless,
                    args=LAUNCH_ARGS,
                )
                # ★ v7: 随机UA + viewport
                search_vp = get_random_viewport()
                search_ua = get_random_ua()
                ctx2 = await browser2.new_context(
                    viewport=search_vp,
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    user_agent=search_ua,
                    storage_state=STORAGE_STATE if has_login else None,
                    extra_http_headers={
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        "sec-ch-ua": get_random_sec_ch_ua(),
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"',
                    },
                )
                await ctx2.add_init_script(STEALTH_INIT_SCRIPT)
                page2 = await ctx2.new_page()
                await self._block_unnecessary_resources(page2)

                # ★ v5: 宽泛XHR监听
                xhr2: List[Dict] = []
                page2.on("response", self._make_broad_response_handler(xhr2))

                search_url = f"https://www.douyin.com/search/{quote(keyword)}?type=general"
                logger.info("  [URL搜索] 导航: %s", search_url[:120])

                try:
                    await page2.goto(search_url, wait_until="domcontentloaded",
                                     timeout=TIMEOUT_PAGE_GOTO * 1000)
                except Exception:
                    logger.info("  [URL搜索] domcontentloaded超时, 继续等待...")
                await asyncio.sleep(1)  # [优化: 2→1]

                # ★ v5: 方法1 - SSR提取
                await asyncio.sleep(random.uniform(0.5, 1.5))  # [优化: 1.5-3.0→0.5-1.5]
                ssr_posts_raw = await self._extract_from_ssr(page2)
                if ssr_posts_raw:
                    ssr_posts = self._parse(ssr_posts_raw, keyword)
                    valid_ssr = [p for p in ssr_posts if self._validate_post(p)]
                    if valid_ssr:
                        logger.info("  [URL搜索-SSR] ✅ %d条有效帖", len(valid_ssr))
                        posts.extend(valid_ssr)

                # ★ v5: 方法2 - 滚动触发XHR
                # [优化: 12轮→6轮, 等待1.5-3.0→0.5-1.5]
                await self._humanlike_scroll(page2, rounds=6)
                await asyncio.sleep(random.uniform(0.5, 1.5))

                xhr_posts = self._extract_posts_from_xhr_broad(xhr2, keyword)
                if xhr_posts:
                    # 去重(与已有SSR结果)
                    existing_ids = {p.get("post_id", "") for p in posts}
                    new_xhr = [p for p in xhr_posts
                              if self._validate_post(p)
                              and p.get("post_id", "") not in existing_ids]
                    if new_xhr:
                        logger.info("  [URL搜索-XHR] ✅ %d条新帖(去重后)", len(new_xhr))
                        posts.extend(new_xhr)

                # ★ v5: 方法3 - DOM兜底
                if not posts:
                    dom_posts = await self._extract_from_dom(page2, keyword)
                    if dom_posts:
                        logger.info("  [URL搜索-DOM] ✅ %d条兜底帖", len(dom_posts))
                        posts.extend(dom_posts)

                # 清理
                await ctx2.close()
                await browser2.close()
                await pw2.stop()

                if posts:
                    # 回填XHR数据到captured_xhr供调用方诊断
                    captured_xhr.extend(xhr2)
                    valid = [p for p in posts if self._validate_post(p)]
                    logger.info("  [URL搜索] ✅ '%s': %d条 (SSR+XHR+DOM) | 第%d次尝试",
                                keyword, len(valid), attempt)
                    return valid

                # 本轮没数据，记录诊断
                logger.warning("  [URL搜索] ❌ '%s' 第%d次: 0条! XHR=%d SSR=%d",
                               keyword, attempt, len(xhr2),
                               len(ssr_posts_raw) if 'ssr_posts_raw' in dir() else 0)
                for i, cap in enumerate(xhr2[:5]):
                    logger.warning("    XHR[%d]: items=%d keys=%s url=%.100s",
                                  i, len(cap.get("items", [])) if cap.get("items") else 0,
                                  cap.get("top_keys", [])[:6], cap.get("url", "")[:100])
                # ★ v7: 保存诊断文件
                await self._save_debug_screenshot(page2, f"nodata_url_{keyword}_r{attempt}")
                await self._save_debug_html(page2, f"nodata_url_{keyword}_r{attempt}")

            except Exception as e:
                logger.warning("  [URL搜索] ❌ '%s' 异常(第%d次): %s",
                             keyword, attempt, str(e)[:80])

        # 所有重试都失败
        logger.warning("  [URL搜索] ❌ '%s': 全部%d次尝试均返回0条! 请检查登录态和网络",
                     keyword, SEARCH_RETRY_COUNT)
        return []

    # ==================== XHR 数据提取 ====================

    def _extract_posts_from_xhr(
        self, captured_xhr: List[Dict], keyword: str,
    ) -> List[Dict]:
        """从拦截的 XHR 响应中提取帖子列表（白名单端点）"""
        posts = []
        for cap in captured_xhr:
            items = cap.get("items")
            if not items or not isinstance(items, list):
                continue
            extracted = self._parse(items, keyword)
            posts.extend(extracted)

        if posts:
            logger.info("  [XHR提取] 从 %d 个API响应中解析 %d 条帖子",
                       len([c for c in captured_xhr if c.get("items")]), len(posts))
        return posts

    def _extract_posts_from_xhr_broad(
        self, captured_xhr: List[Dict], keyword: str,
    ) -> List[Dict]:
        """
        ★ v4: 宽泛XHR提取 —— 用于搜索页面，不依赖白名单端点。
        从所有截获的XHR响应中递归搜索帖子数据，
        因为搜索结果可能通过多种API路径返回。
        """
        posts = []
        for cap in captured_xhr:
            # 先试标准items字段
            items = cap.get("items")
            if items and isinstance(items, list):
                extracted = self._parse(items, keyword)
                posts.extend(extracted)
                continue

            # ★ 如果没有items，从data字段递归搜索
            data = cap.get("data")
            if data and isinstance(data, (dict, list)):
                found = self._deep_find_posts(data)
                if found:
                    extracted = self._parse(found, keyword)
                    posts.extend(extracted)

        if posts:
            logger.info("  [XHR-宽泛] 从 %d 个API响应中解析 %d 条帖子",
                       len(captured_xhr), len(posts))
        return posts

    # ==================== DOM 提取 ====================

    async def _extract_from_dom(self, page, keyword: str) -> List[Dict]:
        """从页面 DOM 提取视频卡片"""
        await asyncio.sleep(random.uniform(0.5, 1.5))  # [优化: 1.5-3.0→0.5-1.5]

        # 先滚动让更多内容加载
        await self._humanlike_scroll(page, rounds=3)  # [优化: 5→3]

        js_extract = """
        () => {
            const results = [];
            const seen = new Set();

            // ★ v7: 方式A: 找所有 /video/ 和 /note/ 链接
            const videoLinks = document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]');
            for (const a of videoLinks) {
                const vm = a.href.match(/\\/video\\/(\\d+)/);
                const nm = a.href.match(/\\/note\\/(\\d+)/);
                const vid = vm ? vm[1] : (nm ? nm[1] : null);
                if (!vid || seen.has(vid)) continue;
                seen.add(vid);

                // 向上找最接近的内容容器（扩大搜索范围）
                let el = a;
                let maxDepth = 10;
                while (el && maxDepth > 0) {
                    const text = (el.innerText || '').trim();
                    if (text.length > 30) break;
                    el = el.parentElement;
                    maxDepth--;
                }
                if (!el) el = a;

                const rawText = (el.innerText || '').trim();
                const lines = rawText.split('\\n')
                    .map(l => l.trim())
                    .filter(l => l.length > 0);

                let desc = '';
                let authorName = '';
                for (const line of lines) {
                    if (!desc && line.length > 10) desc = line;
                    else if (desc && line.length >= 2 && line.length < 40
                             && !line.startsWith('@') && !line.startsWith('#')
                             && !/^\\d/.test(line)) {
                        authorName = line;
                        break;
                    }
                }

                results.push({
                    aweme_id: vid,
                    desc: desc || lines[0] || '',
                    author: authorName || '',
                    raw_lines: lines.slice(0, 5),
                });
                if (results.length >= 50) break;
            }

            // ★ v7: 方式B: data-e2e 属性查找视频卡片
            if (results.length === 0) {
                const searchCards = document.querySelectorAll(
                    '[data-e2e*="search"], [data-e2e*="video"], [data-e2e*="card"], '
                    + '[class*="search-result"], [class*="video-card"], [class*="result-card"], '
                    + '[class*="J0DNS"], [class*="TQHrx"], [class*="Hi_8z"]'
                );
                for (const card of searchCards) {
                    const text = (card.innerText || '').trim();
                    const link = card.querySelector('a[href*="/video/"], a[href*="/note/"]');
                    const href = link ? link.href : '';
                    const vm = href.match(/\\/video\\/(\\d+)/);
                    const nm = href.match(/\\/note\\/(\\d+)/);
                    const vid = vm ? vm[1] : (nm ? nm[1] : null);
                    if (!vid || seen.has(vid) || text.length < 20) continue;
                    seen.add(vid);

                    const lines = text.split('\\n').filter(l => l.trim());
                    results.push({
                        aweme_id: vid,
                        desc: lines[0] || '',
                        author: lines[1] || '',
                        raw_lines: lines.slice(0, 5),
                    });
                    if (results.length >= 30) break;
                }
            }

            // ★ v7: 方式C: 找包含 aweme_id 的任意标签（SSR数据残留）
            if (results.length === 0) {
                const allElements = document.querySelectorAll('[data-id], [data-aweme-id]');
                for (const el of allElements) {
                    const id = el.getAttribute('data-id') || el.getAttribute('data-aweme-id');
                    if (id && /\\d{15,}/.test(id) && !seen.has(id)) {
                        seen.add(id);
                        results.push({
                            aweme_id: id,
                            desc: (el.innerText || '').trim().slice(0, 300),
                            author: '',
                            raw_lines: [],
                        });
                        if (results.length >= 20) break;
                    }
                }
            }

            // ★ v7: 方式D: 页面上所有有 innerText 的元素中匹配 aweme_id 模式
            if (results.length === 0) {
                // 匹配所有可能是帖子ID的19位数字
                const bodyText = document.body.innerText || '';
                const idMatches = bodyText.matchAll(/\\b(\\d{17,20})\\b/g);
                const foundCount = 0;
                for (const m of idMatches) {
                    if (foundCount >= 20) break;
                    const potentialId = m[1];
                    if (!seen.has(potentialId)) {
                        seen.add(potentialId);
                        results.push({
                            aweme_id: potentialId,
                            desc: '(来自页面文本)',
                            author: '',
                            raw_lines: [],
                        });
                    }
                }
            }

            return JSON.stringify(results);
        }
        """

        try:
            raw = await asyncio.wait_for(page.evaluate(js_extract), timeout=TIMEOUT_PAGE_EVAL)
            dom_items = json.loads(raw)
            logger.info("  [DOM提取] 找到 %d 个视频卡片", len(dom_items))

            if not dom_items:
                return []

            posts = []
            for item in dom_items:
                aweme_id = item.get("aweme_id", "")
                if not aweme_id:
                    continue
                posts.append({
                    "post_id": f"dy_{aweme_id}",
                    "platform": "douyin",
                    "account_id": "",
                    "account_name": (item.get("author", "") or "未知")[:50],
                    "account_level": "10w_plus",
                    "follower_count": 0,
                    "post_type": "video",
                    "post_content": (item.get("desc", "") or "(无描述)")[:500],
                    "post_url": f"https://www.douyin.com/video/{aweme_id}",
                    "like_count": 0, "comment_count": 0, "share_count": 0,
                    "post_time": datetime.now(), "collect_time": datetime.now(),
                    "search_keyword": keyword,
                })
            return posts

        except asyncio.TimeoutError:
            logger.warning("  [DOM提取] 超时")
        except Exception as e:
            logger.warning("  [DOM提取] 异常: %s", str(e)[:80])
        return []

    # ==================== 数据解析 ====================

    def _parse(self, items: List[Dict], keyword: str) -> List[Dict]:
        """★ 解析帖子数据 - 区分视频(video)和图文(image_text)，提取完整互动数据"""
        posts = []
        for item in items:
            if not isinstance(item, dict):
                continue

            aweme_id = str(
                item.get("aweme_id")
                or item.get("aweme_info", {}).get("aweme_id")
                or item.get("video_id")
                or item.get("aweme", {}).get("aweme_id")
                or ""
            )
            if not aweme_id:
                continue

            author = item.get("author") or item.get("author_info") or {}
            if not isinstance(author, dict):
                author = {}

            stats = item.get("statistics") or item.get("stats") or {}
            if not isinstance(stats, dict):
                stats = {}

            # ★ v4: 支持图文类型
            images = item.get("images") or item.get("image_infos") or []
            is_image_text = bool(images and len(images) > 0)

            desc = str(item.get("desc") or item.get("title") or item.get("share_info", {}).get("share_desc", "") or "")
            nickname = str(author.get("nickname") or author.get("short_id") or author.get("unique_id") or "")
            uid = str(author.get("uid") or author.get("short_id") or author.get("sec_uid") or "")
            sec_uid = str(author.get("sec_uid") or author.get("uid") or author.get("author_id") or "")
            fid = int(author.get("follower_count") or author.get("fans_count") or author.get("followers") or 0)

            # ★ v5: 更健壮的时间戳处理（支持多种格式）
            ts = (item.get("create_time") or item.get("createTime")
                  or item.get("create_timestamp") or item.get("timestamp") or 0)

            if isinstance(ts, (int, float)) and ts > 0:
                # 秒级时间戳（10位，如 1700000000）
                if ts < 10000000000:
                    pt = datetime.fromtimestamp(ts)
                # 毫秒级时间戳（13位，如 1700000000000）
                elif ts < 10000000000000:
                    pt = datetime.fromtimestamp(ts / 1000)
                # 微秒级时间戳（16位）
                else:
                    pt = datetime.fromtimestamp(ts / 1000000)
            elif isinstance(ts, str) and ts.strip():
                ts = ts.strip()
                try:
                    if ts.isdigit():
                        ts_int = int(ts)
                        if ts_int < 10000000000:
                            pt = datetime.fromtimestamp(ts_int)
                        elif ts_int < 10000000000000:
                            pt = datetime.fromtimestamp(ts_int / 1000)
                        else:
                            pt = datetime.fromtimestamp(ts_int / 1000000)
                    elif "T" in ts or "-" in ts:
                        # ISO格式: "2024-01-15T10:30:00" 或 "2024-01-15 10:30:00"
                        ts_clean = ts.replace("T", " ").split(".")[0].split("+")[0].strip()
                        pt = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S")
                    else:
                        pt = datetime.now()
                except (ValueError, OSError):
                    pt = datetime.now()
            else:
                pt = datetime.now()

            # ★ v4: 更丰富的互动数据
            like = int(stats.get("digg_count") or stats.get("like_count")
                      or stats.get("diggCount") or stats.get("admire_count") or 0)
            comment = int(stats.get("comment_count") or stats.get("commentCount")
                         or stats.get("reply_count") or 0)
            share = int(stats.get("share_count") or stats.get("shareCount")
                       or stats.get("forward_count") or 0)
            collect = int(stats.get("collect_count") or stats.get("collection_count") or 0)

            # ★ v4: 提取标签
            text_extra = item.get("text_extra") or []
            tags = []
            if isinstance(text_extra, list):
                for te in text_extra:
                    if isinstance(te, dict) and te.get("hashtag_name"):
                        tags.append(f"#{te['hashtag_name']}")
            tag_str = " ".join(tags[:8]) if tags else ""

            # ★ v4: 拼接完整内容（描述 + 标签）
            full_content = desc
            if tag_str and tag_str not in full_content:
                full_content = f"{desc} {tag_str}" if desc else tag_str

            # ★ v6: 严格24小时时间窗口过滤
            cutoff_24h = datetime.now() - timedelta(hours=POST_MAX_AGE_HOURS)
            if pt and pt < cutoff_24h:
                continue  # 超过24小时的帖子跳过

            posts.append({
                "post_id": f"dy_{aweme_id}",
                "platform": "douyin",
                "account_id": hashlib.md5(nickname.encode()).hexdigest()[:16] if nickname else uid[:16],
                "account_name": nickname or "未知",
                "account_level": AccountPool.classify(nickname, fid),
                "follower_count": fid,
                "sec_uid": sec_uid,
                "post_type": "image_text" if is_image_text else "video",
                "post_content": (full_content or "(无描述)")[:500],
                "post_url": f"https://www.douyin.com/video/{aweme_id}",
                "like_count": like,
                "comment_count": comment,
                "share_count": share,
                "collect_count": collect,  # ★ v4: 新增收藏数
                "post_time": pt,
                "collect_time": datetime.now(),
                "search_keyword": keyword,
            })
        return posts

    def _save_to_db(self):
        try:
            from module7_data_storage import MySQLManager
            db = MySQLManager()
            df = pd.DataFrame(self._posts)
            n = db.insert_raw_posts(df)
            logger.info("入库 %d 条", n)
        except Exception as e:
            logger.warning("入库失败: %s", e)

    # ==================== 账号池清洗 ====================

    @staticmethod
    def _validate_account_pool():
        """清理账号池：排除黑名单中的非财经账号"""
        pool = AccountPool.load()
        if not pool:
            return
        cleaned = {}
        removed = []
        for name, info in pool.items():
            is_blacklisted = any(bk in name for bk in FINANCE_ACCOUNT_BLACKLIST)
            is_verified = info.get("is_finance_verified", False)
            if is_blacklisted and not is_verified:
                removed.append(name)
                continue
            cleaned[name] = info
        if removed:
            logger.info("  [账号池清洗] 移除 %d 个非财经账号: %s...",
                       len(removed), removed[:5])
            AccountPool.save(cleaned)

    # ==================== 财经账号发现 ====================

    async def _discover_finance_accounts(self, page):
        """
        账号发现阶段：用高精度财经搜索词搜索，发现财经账号。
        这些搜索词几乎不会被非财经账号使用。
        """
        discovery_keywords = [
            "今日A股复盘", "股市收评", "明日走势预测",
            "沪深300分析", "板块轮动策略", "北向资金流向",
            "基金操作策略", "技术分析教学",
        ]
        random.shuffle(discovery_keywords)
        # 只跑前4个发现词
        todo = discovery_keywords[:4]

        logger.info("  发现关键词: %s", todo)
        discovered_accounts = []

        # 新标签页用于发现
        discovery_page = await page.context.new_page()
        discovery_xhr: List[Dict] = []
        discovery_page.on("response", self._make_response_handler(discovery_xhr))

        for kw in todo:
            discovery_xhr.clear()
            logger.info("  [发现] 搜索: '%s'", kw)
            try:
                posts = await self._search_via_input(discovery_page, kw, discovery_xhr)
            except Exception:
                posts = []

            for p in posts:
                acc_name = p.get("account_name", "").strip()
                if not acc_name or acc_name == "未知":
                    continue
                is_fin, conf = self._is_finance_account(acc_name, p.get("follower_count", 0))
                if is_fin and conf >= 0.7:
                    p["_is_finance_account"] = True
                    p["_account_finance_confidence"] = conf
                    discovered_accounts.append(p)

            logger.info("  [发现] '%s' → %d 个财经账号", kw,
                       len([a for a in discovered_accounts if a["search_keyword"] == kw]))
            await self._human_delay(1, 2)  # [优化: 5-12s → 1-2s]

        await discovery_page.close()

        if discovered_accounts:
            AccountPool.update(discovered_accounts)
            logger.info("  ✅ 账号发现完成: 新增/更新 %d 个财经账号", len(discovered_accounts))
        else:
            logger.info("  ⚠️ 账号发现未找到新的财经账号")

        return discovered_accounts

    async def _discover_finance_accounts_expanded(
        self, page, discovery_xhr: List[Dict] = None,
    ):
        """
        v3 扩展版账号发现：使用更多高精度词 + 更多轮次，
        目标是构建 ≥1200 个财经账号的账号池。
        复用传入的 page 和 xhr 列表（避免重复创建标签页）。
        """
        discovery_keywords = [
            # === 高精度 — 几乎只有财经号会发 ===
            "今日A股复盘", "股市收评", "明日走势预测",
            "沪深300分析", "板块轮动策略", "北向资金流向",
            "基金操作策略", "技术分析教学",
            "A股收盘", "大盘分析", "龙虎榜解读",
            "ETF投资策略", "财报解读", "涨停板复盘",
            "证券分析", "宏观经济解读", "投资笔记",
            # === 四大指数相关 ===
            "上证指数今日", "上证综指分析", "上证指数走势",
            "沪深300今日", "沪深300分析", "沪深300ETF",
            "中证500指数", "中证500分析", "中证500走势",
            "中证1000指数", "中证1000分析", "中证1000走势",
            "指数基金定投", "宽基指数分析", "大盘行情走势",
            # ★ v5: 新增覆盖更多财经子领域
            "量化交易策略", "A股操盘计划", "基金定投实盘",
            "可转债投资", "行业研究报告", "股票技术分析",
            "市场情绪分析", "北向资金今日", "主力资金流向",
            "打板策略", "短线交易技巧", "价值投资实战",
            "期货交易策略", "证券从业考试", "CFA备考",
            "IPO分析", "港股打新", "美股投资策略",
            # ★ v6: 大幅扩展关键词(覆盖更多细分领域+不同时段)
            "A股早评", "A股午评", "A股晚报", "盘前必读",
            "收盘点评", "每日复盘", "明日策略", "操盘计划",
            "涨停板复盘", "跌停板复盘", "涨停分析",
            "北向资金实时", "北向资金净流入", "北向资金净流出",
            "主力资金净流入", "主力资金净流出", "机构龙虎榜",
            "热点板块分析", "板块轮动", "领涨板块", "领跌板块",
            "个股研报", "行业研报", "公司深度研究",
            "业绩预告解读", "年报分析", "一季报", "中报", "三季报",
            "股息率", "分红率", "市盈率", "市净率", "ROE分析",
            "技术面分析", "K线形态", "均线系统", "量价关系",
            "MACD金叉", "MACD死叉", "布林带", "RSI指标",
            "波浪理论", "缠论", "道氏理论", "江恩理论",
            "宏观经济数据", "GDP数据", "CPI数据", "PMI数据",
            "央行政策", "降准降息", "MLF操作", "LPR利率",
            "美联储议息", "人民币汇率", "美元指数",
            "煤炭板块分析", "电力板块分析", "银行股分析",
            "保险股分析", "地产股分析", "基建板块",
            "消费板块分析", "医药板块分析", "科技板块分析",
            "新能源车板块", "光伏产业链", "风电板块",
            "锂电产业链", "储能板块", "氢能源",
            "人工智能板块", "半导体产业", "芯片产业链",
            "数字经济", "信创板块", "数据要素",
            "国企改革", "央企概念", "中特估",
            "高股息策略", "红利指数", "低估值策略",
            "成长股投资", "价值投资理念", "趋势交易",
            "期权策略", "股指期货", "商品期货",
            "理财规划", "资产配置", "财富管理",
        ]
        random.shuffle(discovery_keywords)
        todo = discovery_keywords[:DISCOVERY_KW_COUNT]

        logger.info("  发现关键词(%d个): %s", len(todo), todo)
        discovered_accounts = []

        for round_idx, kw in enumerate(todo):
            if discovery_xhr is not None:
                discovery_xhr.clear()

            logger.info("  [发现 %d/%d] 搜索: '%s'",
                         round_idx + 1, len(todo), kw)
            try:
                # ★ v5: 直接URL搜索(更可靠)，用独立浏览器
                posts = await self._search_via_url(kw, discovery_xhr or [])
            except Exception:
                try:
                    # 回退到页面输入搜索
                    posts = await self._search_via_input(page, kw, discovery_xhr or [])
                except Exception:
                    posts = []

            new_in_round = 0
            posts_saved = 0
            for p in posts:
                acc_name = p.get("account_name", "").strip()
                sec = p.get("sec_uid", "").strip()

                # ★ 先判断账号是否是财经账号
                is_fin, conf = self._is_finance_account(
                    acc_name, p.get("follower_count", 0)
                )

                # ★ 严格过滤: 只有财经账号的帖子或内容含强财经信号的帖子才保存
                content = p.get("post_content", "")
                is_content_finance = self._is_finance_related(
                    content=content,
                    account_name=acc_name,
                    is_finance_account=is_fin,
                    account_confidence=conf,
                )

                pid = p.get("post_id", "")
                if pid and pid not in self._seen_ids and is_content_finance:
                    self._seen_ids.add(pid)
                    self._posts.append(p)
                    posts_saved += 1

                if not acc_name or acc_name == "未知":
                    continue
                if is_fin and conf >= 0.7:
                    p["_is_finance_account"] = True
                    p["_account_finance_confidence"] = conf
                    discovered_accounts.append(p)
                    new_in_round += 1

            logger.info("  [发现 %d/%d] '%s' → +%d 账号, +%d 帖",
                         round_idx + 1, len(todo), kw, new_in_round, posts_saved)
            await self._human_delay(1, 3)  # [优化: 4-10s → 1-3s]

        if discovered_accounts:
            AccountPool.update(discovered_accounts)
            logger.info("  ✅ 账号发现完成: 新增/更新 %d 个财经账号",
                         len(discovered_accounts))
        else:
            logger.info("  ⚠️  账号发现未找到新的财经账号（账号池可能已充足）")

        return discovered_accounts

    # ==================== 财经账号主页采集 ====================

    async def _scrape_finance_profiles(self, browser_context, max_accounts: int = 500):
        """
        ★ 主力采集阶段: 用 sec_uid 直链访问财经账号主页，批量抓取24h帖子。
        每条都来自确认的财经账号，质量最高。
        """
        candidates = AccountPool.get_unscraped_today(limit=max_accounts)
        if not candidates:
            logger.info("  ℹ️  今天所有财经账号已采集完毕，跳过主页采集")
            return 0

        logger.info("  ★ 主力采集: %d 个财经账号主页 (今天未采集)", len(candidates))
        total_new = 0
        profile_page = await browser_context.new_page()
        profile_xhr: List[Dict] = []
        profile_page.on("response", self._make_response_handler(profile_xhr))

        for idx, (acc_name, sec_uid, followers) in enumerate(candidates):
            profile_xhr.clear()
            logger.info("  [主页 %d/%d] '%s' (粉丝 %d)",
                       idx + 1, len(candidates), acc_name[:25], followers)

            try:
                profile_url = f"https://www.douyin.com/user/{sec_uid}"
                await asyncio.wait_for(
                    profile_page.goto(profile_url, wait_until="domcontentloaded",
                                     timeout=TIMEOUT_PAGE_GOTO * 1000),
                    timeout=TIMEOUT_PAGE_GOTO,
                )
                await asyncio.sleep(3)

                # ★ 滚动到底部加载帖子
                await self._scroll_to_load(profile_page, rounds=25, wait_between=1.2)

                posts = self._extract_posts_from_xhr(profile_xhr, acc_name)
                if not posts:
                    posts = await self._extract_from_dom(profile_page, acc_name)

                if not posts:
                    # 仍标记已采集，避免每天重试无效账号
                    AccountPool.mark_scraped_today(acc_name)
                    continue

                new = 0
                for p in posts:
                    if p["post_id"] in self._seen_ids:
                        continue
                    self._seen_ids.add(p["post_id"])
                    p["_is_finance_account"] = True
                    p["_account_finance_confidence"] = 0.85
                    p["account_name"] = acc_name
                    p["sec_uid"] = sec_uid

                    if not self._is_finance_related(
                        content=p.get("post_content", ""),
                        account_name=acc_name,
                        is_finance_account=True,
                        account_confidence=0.85,
                    ):
                        continue

                    self._posts.append(p)
                    new += 1

                total_new += new
                # ★ 标记今天已采集
                AccountPool.mark_scraped_today(acc_name)
                logger.info("  [主页] '%s': +%d 条 (累计 %d)",
                           acc_name[:20], new, len(self._posts))

            except asyncio.TimeoutError:
                AccountPool.mark_scraped_today(acc_name)  # 超时也标记
                logger.warning("  [主页] '%s' 超时", acc_name[:20])
            except Exception as e:
                logger.warning("  [主页] '%s' 异常: %s", acc_name[:20], str(e)[:60])

            if (idx + 1) % 20 == 0:
                delay = random.uniform(8, 15)
                logger.info("  [主页] 已处理 %d/%d, 休息 %.1f 秒...",
                           idx + 1, len(candidates), delay)
                await asyncio.sleep(delay)
            else:
                await self._human_delay(0.5, 1.5)

        await profile_page.close()
        pool_size = len(AccountPool.load())
        logger.info("  ✅ 主力采集完成: %d 个账号 → %d 条新帖 | 账号池总数: %d",
                   len(candidates), total_new, pool_size)
        return total_new

    # ==================== v3 并发采集核心 ====================

    # ---- 帖子数据源提取 ----

    async def _extract_from_ssr(self, page) -> List[Dict]:
        """
        ★ v3 主提取方式: 从页面SSR内嵌数据中提取帖子。
        Douyin 用户主页的 HTML 中包含 <script id="RENDER_DATA"> 或
        window.__INITIAL_STATE__ 等内嵌JSON，比拦截XHR更可靠:
          - 不受签名/X-Bogus影响
          - 页面加载即存在，无需等待异步请求
          - 数据结构稳定，不会被加密
        返回: 帖子列表 [{aweme_id, desc, author, ...}, ...]
        """
        ssr_posts = []

        # 方法1: RENDER_DATA (新版抖音主页)
        try:
            raw = await page.evaluate("""
                () => {
                    const el = document.getElementById('RENDER_DATA');
                    if (el && el.textContent) {
                        try {
                            return decodeURIComponent(el.textContent);
                        } catch(e) {
                            return el.textContent;
                        }
                    }
                    return null;
                }
            """)
            if raw and len(raw) > 500:
                data = json.loads(raw)
                # 递归找帖子列表
                found = self._deep_find_posts(data)
                if found:
                    ssr_posts.extend(found)
                    logger.info("  [SSR-RENDER_DATA] 提取 %d 条帖子", len(found))
        except Exception as e:
            logger.debug("  [SSR] RENDER_DATA 提取失败: %s", str(e)[:60])

        # 方法2: window.__INITIAL_STATE__ 或 __UNIVERSAL_INITIAL_STATE__
        if not ssr_posts:
            try:
                raw = await page.evaluate("""
                    () => {
                        const keys = ['__UNIVERSAL_INITIAL_STATE__',
                                      '__INITIAL_STATE__',
                                      '_SSR_HYDRATED_DATA__',
                                      '__NEXT_DATA__'];
                        for (const k of keys) {
                            if (window[k]) return JSON.stringify(window[k]);
                        }
                        return null;
                    }
                """)
                if raw and len(raw) > 500:
                    data = json.loads(raw)
                    found = self._deep_find_posts(data)
                    if found:
                        ssr_posts.extend(found)
                        logger.info("  [SSR-INITIAL_STATE] 提取 %d 条帖子", len(found))
            except Exception as e:
                logger.debug("  [SSR] INITIAL_STATE 提取失败: %s", str(e)[:60])

        # 方法3: 所有 <script> 标签中搜索 aweme_id
        if not ssr_posts:
            try:
                raw = await page.evaluate("""
                    () => {
                        const scripts = document.querySelectorAll('script');
                        for (const s of scripts) {
                            const text = s.textContent || s.innerText || '';
                            if (text.includes('"aweme_id"') && text.length > 500) {
                                return text;
                            }
                        }
                        return null;
                    }
                """)
                if raw and len(raw) > 500:
                    import re as _re
                    # ★ v4: 更宽松的JSON提取正则
                    json_match = _re.search(r'\{[^}]*"aweme_id"[^}]*\}', raw, _re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                            found = self._deep_find_posts(data)
                            if found:
                                ssr_posts.extend(found)
                                logger.info("  [SSR-script] 提取 %d 条帖子", len(found))
                        except Exception:
                            pass
            except Exception as e:
                logger.debug("  [SSR] script搜索失败: %s", str(e)[:60])

        # ★ v4 方法4: 直接从 window.__M__(或类似) 全局变量抓取
        if not ssr_posts:
            try:
                raw = await page.evaluate("""
                    () => {
                        // 遍历 window 上的所有键，找到含帖子数据的变量
                        const candidates = [];
                        for (const k of Object.keys(window)) {
                            try {
                                const v = window[k];
                                if (v && typeof v === 'object' && !Array.isArray(v)) {
                                    const s = JSON.stringify(v).substring(0, 200);
                                    if (s.includes('aweme_id')) {
                                        candidates.push(k);
                                    }
                                }
                            } catch(e) {}
                        }
                        if (candidates.length > 0) {
                            const best = candidates.find(k => k.includes('state') || k.includes('data') || k.includes('render')) || candidates[0];
                            return JSON.stringify(window[best]);
                        }
                        return null;
                    }
                """)
                if raw and len(raw) > 500 and '"aweme_id"' in raw:
                    try:
                        data = json.loads(raw)
                        found = self._deep_find_posts(data)
                        if found:
                            ssr_posts.extend(found)
                            logger.info("  [SSR-window] 提取 %d 条帖子", len(found))
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("  [SSR] window遍历失败: %s", str(e)[:60])

        return ssr_posts

    def _deep_find_posts(self, data, max_depth=10) -> List[Dict]:
        """递归搜索JSON中所有包含 aweme_id 的帖子对象"""
        posts = []
        seen_ids = set()

        def _walk(obj, depth=0):
            if depth > max_depth:
                return
            if isinstance(obj, dict):
                # 判断是否是一个帖子对象
                if "aweme_id" in obj and "desc" in obj:
                    aweme_id = str(obj["aweme_id"])
                    if aweme_id not in seen_ids:
                        seen_ids.add(aweme_id)
                        posts.append(obj)
                    return  # 不再深入
                # 继续递归
                for v in obj.values():
                    _walk(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item, depth + 1)

        _walk(data)
        return posts

    # ---- 帖子字段验证 ----

    def _validate_post(self, post: Dict) -> bool:
        """
        ★ v4: 验证帖子数据完整性。
        满足以下条件才认为有效:
          1. aweme_id 或 post_id 存在且长度≥10
          2. post_content 或 desc 长度≥5
          3. account_name 或 author.nickname 存在
        """
        # 必须字段检查
        aweme_id = post.get("aweme_id") or post.get("post_id", "")
        if not aweme_id or len(str(aweme_id)) < 10:
            return False

        # 内容非空
        content = post.get("post_content") or post.get("desc") or ""
        if len(str(content).strip()) < 5:
            return False

        # ★ v4: 优先检查 account_name（_parse产出），再回退到 author 对象
        nickname = post.get("account_name") or ""
        if not nickname:
            author = post.get("author") or {}
            if isinstance(author, dict):
                nickname = author.get("nickname") or author.get("short_id") or author.get("unique_id") or ""
        if not nickname:
            return False

        return True

    # ---- 候选账号 ----

    def _get_scraping_candidates(self, limit: int = MAX_ACCOUNTS_TO_SCRAPE) -> List[tuple]:
        """
        ★ v7: 获取待采集的财经账号列表，大幅放宽门槛以增加采集量。
        分层策略:
          1. 已验证财经账号 + 粉丝≥50万 (top1000级别)
          2. 已验证财经账号 + 粉丝≥10万 (10w_plus级别)
          3. 高置信度(≥0.7) + 粉丝≥10万
          4. ★ v7: 中置信度(≥0.2) + 粉丝≥10万 (放宽门槛)
          5. ★ v7: 任何有sec_uid且粉丝≥5万的账号 (最大化采集量)
        排除今日已采集的账号。
        返回: [(account_name, sec_uid, follower_count, priority), ...]
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        pool = AccountPool.load()
        candidates = []

        for name, info in pool.items():
            sec = str(info.get("sec_uid", "") or "").strip()
            if not sec:
                continue
            if info.get("last_scraped_date") == today_str:
                continue

            followers = int(info.get("follower_count", 0) or 0)
            is_verified = info.get("is_finance_verified", False)
            finance_score = info.get("finance_score", 0.0)

            # ★ v7: 大幅放宽财经过滤 —— 最低门槛降到 0.2
            is_finance = is_verified or finance_score >= 0.2
            if not is_finance:
                # ★ v7: 即使 finance_score 很低，只要有sec_uid且粉丝≥5万也采集
                if followers >= 50000:
                    priority = 5
                else:
                    continue
            else:
                # 计算优先级 (越高越优先)
                if is_verified and followers >= 1000000:
                    priority = 110
                elif is_verified and followers >= 500000:
                    priority = 100
                elif is_verified and followers >= MIN_FOLLOWER_THRESHOLD:
                    priority = 85
                elif is_verified:
                    priority = 70
                elif finance_score >= 0.7 and followers >= 1000000:
                    priority = 65
                elif finance_score >= 0.7 and followers >= 500000:
                    priority = 60
                elif finance_score >= 0.7 and followers >= MIN_FOLLOWER_THRESHOLD:
                    priority = 50
                elif finance_score >= 0.4 and followers >= 500000:
                    priority = 45
                elif finance_score >= 0.4 and followers >= MIN_FOLLOWER_THRESHOLD:
                    priority = 35
                elif finance_score >= 0.2 and followers >= MIN_FOLLOWER_THRESHOLD:
                    priority = 25
                elif followers >= 50000:
                    priority = 15
                else:
                    priority = 8

            # 粉丝数加权
            if followers >= 5000000:
                priority += 20
            elif followers >= 2000000:
                priority += 15
            elif followers >= 1000000:
                priority += 10
            elif followers >= 500000:
                priority += 5

            candidates.append((name, sec, followers, priority))

        # ★ v7: 按粉丝数严格降序排列
        candidates.sort(key=lambda x: (x[2], x[3]), reverse=True)

        # ★ v7: 取前N名（增加到1500以确保覆盖）
        top_n = min(limit, max(COLLECTION_TOP_N, 1500))
        selected = candidates[:top_n]

        if selected:
            top_followers = selected[0][2]
            min_followers = selected[-1][2]
            logger.info("  [候选账号 v7] 池=%d 待采=%d topN=%d | 粉丝范围: %d~%d",
                         len(pool), len(candidates), len(selected),
                         min_followers, top_followers)
        else:
            logger.warning("  [候选账号] ⚠️ 池=%d 待采=0! 账号池可能为空或所有账号今日已采集", len(pool))
        return selected

    async def _scrape_profiles_concurrent(
        self, browser, accounts: List[tuple], max_concurrency: int = 10,
    ) -> int:
        """
        ★ v3 核心: 多Context并发采集财经账号主页

        每个worker创建独立的浏览器上下文(隔离cookie/存储),
        所有worker并发执行, 目标5分钟内完成1200+账号。

        Args:
            browser: 共享的浏览器实例
            accounts: [(name, sec_uid, followers, priority), ...]
            max_concurrency: 并发上下文数

        Returns:
            新增帖子总数
        """
        if not accounts:
            logger.info("  无待采集账号")
            return 0

        # 将账号均匀分配到各worker
        batches = [[] for _ in range(max_concurrency)]
        for i, acc in enumerate(accounts):
            batches[i % max_concurrency].append(acc)

        # 过滤空批次
        active_batches = [(i, b) for i, b in enumerate(batches) if b]
        logger.info("  ★ 并发采集启动: %d 个账号 → %d 个worker (每worker %d~%d 个账号)",
                     len(accounts), len(active_batches),
                     min(len(b) for _, b in active_batches),
                     max(len(b) for _, b in active_batches))

        t_start = datetime.now()

        # ===== Worker定义 =====
        async def _worker(batch: List[tuple], worker_id: int) -> List[Dict]:
            """单个worker: 创建独立context → 顺序处理分配的账号 ★ v7: 随机化指纹"""
            has_login = os.path.exists(STORAGE_STATE)
            w_vp = get_random_viewport()
            w_ua = get_random_ua()

            context = await browser.new_context(
                viewport=w_vp,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                user_agent=w_ua,
                storage_state=STORAGE_STATE if has_login else None,
                extra_http_headers={
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "sec-ch-ua": get_random_sec_ch_ua(),
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                },
            )
            await context.add_init_script(STEALTH_INIT_SCRIPT)

            page = await context.new_page()
            await self._block_unnecessary_resources(page)
            xhr_data: List[Dict] = []
            page.on("response", self._make_response_handler(xhr_data))

            results = []
            cutoff_time = datetime.now() - timedelta(hours=POST_MAX_AGE_HOURS)
            batch_size = len(batch)

            for idx, (acc_name, sec_uid, followers, priority) in enumerate(batch):
                xhr_data.clear()

                try:
                    # 导航到博主主页
                    profile_url = f"https://www.douyin.com/user/{sec_uid}"
                    await asyncio.wait_for(
                        page.goto(profile_url, wait_until="domcontentloaded",
                                  timeout=PROFILE_PAGE_TIMEOUT_S * 1000),
                        timeout=PROFILE_PAGE_TIMEOUT_S,
                    )
                    # ★ v3: 优先从SSR内嵌数据提取（最可靠）
                    await asyncio.sleep(0.1)  # [优化: 0.5→0.1]

                    posts_raw = await self._extract_from_ssr(page)
                    source = "SSR"

                    if posts_raw:
                        # SSR返回原始对象，需要通过_parse标准化
                        posts = self._parse(posts_raw, acc_name)
                    else:
                        posts = []

                    # SSR失败 → 滚动触发XHR
                    if not posts:
                        for _ in range(PROFILE_SCROLL_ROUNDS):
                            await page.evaluate(
                                "window.scrollTo(0, document.body.scrollHeight)"
                            )
                            await asyncio.sleep(PROFILE_SCROLL_INTERVAL_S)
                        posts = self._extract_posts_from_xhr(xhr_data, acc_name)
                        source = "XHR"

                    # XHR也失败 → DOM兜底(DOM返回已格式化帖)
                    if not posts:
                        posts = await self._extract_from_dom(page, acc_name)
                        source = "DOM"

                    # ★ v3: 验证帖子字段完整性
                    posts = [p for p in posts if self._validate_post(p)]
                    if not posts:
                        logger.debug("  [W%d] '%s': %s提取后验证失败(0条有效)",
                                     worker_id, acc_name[:20], source)
                    elif source == "SSR":
                        logger.debug("  [W%d] '%s': SSR提取 %d 条有效帖",
                                     worker_id, acc_name[:20], len(posts))

                except asyncio.TimeoutError:
                    await AccountPool.mark_scraped_today_async(acc_name)
                    if (idx + 1) % 30 == 0:
                        logger.info("  [W%d] %d/%d (含超时跳过)", worker_id, idx + 1, batch_size)
                    continue
                except Exception:
                    await AccountPool.mark_scraped_today_async(acc_name)
                    continue

                # 处理该账号的帖子
                new_for_account = 0
                for p in posts:
                    pid = p.get("post_id", "")
                    if not pid or pid in self._seen_ids:
                        continue

                    pt = p.get("post_time")
                    if pt is not None and isinstance(pt, datetime) and pt < cutoff_time:
                        continue

                    self._seen_ids.add(pid)
                    p["_is_finance_account"] = True
                    p["_account_finance_confidence"] = 0.85
                    p["account_name"] = acc_name
                    p["sec_uid"] = sec_uid
                    p["account_level"] = (
                        "top1000" if followers >= 500000 else "10w_plus"
                    )
                    p["follower_count"] = followers

                    # 快速内容过滤（跳过明显非财经+广告）
                    if not self._is_finance_related(
                        content=p.get("post_content", ""),
                        account_name=acc_name,
                        is_finance_account=True,
                        account_confidence=0.85,
                    ):
                        continue

                    results.append(p)
                    new_for_account += 1

                await AccountPool.mark_scraped_today_async(acc_name)

                # 进度日志
                if (idx + 1) % 25 == 0:
                    elapsed = (datetime.now() - t_start).total_seconds()
                    logger.info("  [W%d] %d/%d 账号 | +%d 帖 | %.0fs",
                                worker_id, idx + 1, batch_size,
                                len(results), elapsed)

            await page.close()
            await context.close()
            return results

        # ===== 并发执行所有worker =====
        worker_tasks = [
            _worker(batch, wid) for wid, batch in active_batches
        ]
        all_results = await asyncio.gather(*worker_tasks, return_exceptions=True)

        # 合并结果
        for result in all_results:
            if isinstance(result, list):
                self._posts.extend(result)
            elif isinstance(result, Exception):
                logger.error("  Worker异常: %s", str(result)[:200])

        elapsed = (datetime.now() - t_start).total_seconds()
        new_posts = sum(
            len(r) for r in all_results if isinstance(r, list)
        )
        logger.info("  ✅ 并发采集完成: %d 个账号 → %d 条新帖 | 耗时 %.1f 秒",
                     len(accounts), new_posts, elapsed)

        # 诊断: 输出发现的所有API端点
        if self._discovered_endpoints:
            logger.info("  [诊断] 发现 %d 个未使用API端点:", len(self._discovered_endpoints))
            for ep in sorted(self._discovered_endpoints)[:15]:
                logger.info("    %s", ep[:140])

        return new_posts

    # ==================== v3.1 真多线程采集 + 自动账号发现 ====================

    async def _discover_from_profile(self, page, source_account: str) -> int:
        """
        ★ 自动账号发现: 从主页DOM中提取"相关推荐"账号。
        不依赖搜索API，无需手动导入种子，账号池自我扩充。
        返回新增账号数。
        """
        discovered = 0
        try:
            # 从DOM提取推荐账号卡片
            cards = await page.evaluate("""
                () => {
                    const results = [];
                    const seen = new Set();

                    // 方式1: 找包含sec_uid的链接（推荐账号卡片）
                    const userLinks = document.querySelectorAll(
                        'a[href*="/user/"], [data-e2e*="user"], [data-e2e*="author"]'
                    );
                    for (const el of userLinks) {
                        const href = el.href || el.getAttribute('href') || '';
                        const m = href.match(/\\/user\\/([a-zA-Z0-9_-]+)/);
                        if (!m || seen.has(m[1])) continue;
                        seen.add(m[1]);

                        // 获取父容器文本（含昵称、粉丝数）
                        const container = el.closest('[class*="card"], [class*="item"], [class*="list"]') || el.parentElement;
                        const text = (container?.innerText || el.innerText || '').trim();
                        const lines = text.split('\\n').filter(l => l.trim());

                        let nickname = lines[0] || '';
                        let followers = 0;
                        for (const line of lines) {
                            const fm = line.match(/([\\d.]+)\\s*([万万千])?\\s*(?:粉丝|获赞)/);
                            if (fm) {
                                let n = parseFloat(fm[1]);
                                if (fm[2] === '万' || fm[2] === '万') n *= 10000;
                                else if (fm[2] === '千' || fm[2] === '千') n *= 1000;
                                followers = Math.floor(n);
                                break;
                            }
                        }

                        results.push({
                            sec_uid: m[1],
                            nickname: nickname,
                            followers: followers
                        });
                        if (results.length >= 20) break;
                    }

                    // ★ v5: 方式2 - 从SSR数据中批量提取推荐用户
                    try {
                        const rd = document.getElementById('RENDER_DATA');
                        if (rd && rd.textContent) {
                            const decoded = decodeURIComponent(rd.textContent);
                            // 匹配所有sec_uid（不限于MS4w开头，覆盖更多格式）
                            const userMatches = decoded.matchAll(
                                /"sec_uid"\s*:\s*"([^"]+)"/g
                            );
                            for (const um of userMatches) {
                                const suid = um[1];
                                if (!seen.has(suid) && suid.length >= 10) {
                                    seen.add(suid);
                                    results.push({sec_uid: suid, nickname: '', followers: 0});
                                    if (results.length >= 100) break;
                                }
                            }
                        }
                    } catch(e) {}

                    // ★ v5: 方式3 - 从window.__INITIAL_STATE__提取
                    try {
                        const keys = ['__UNIVERSAL_INITIAL_STATE__',
                                      '__INITIAL_STATE__',
                                      '_SSR_HYDRATED_DATA__'];
                        for (const k of keys) {
                            if (window[k] && typeof window[k] === 'object') {
                                const s = JSON.stringify(window[k]);
                                const matches = s.matchAll(/"sec_uid"\s*:\s*"([^"]+)"/g);
                                for (const um of matches) {
                                    const suid = um[1];
                                    if (!seen.has(suid) && suid.length >= 10) {
                                        seen.add(suid);
                                        results.push({sec_uid: suid, nickname: '', followers: 0});
                                        if (results.length >= 100) break;
                                    }
                                }
                                break;
                            }
                        }
                    } catch(e) {}

                    return JSON.stringify(results);
                }
            """)
            related = json.loads(cards)
        except Exception:
            return 0

        for acc in related:
            sec = str(acc.get("sec_uid", "") or "").strip()
            name = str(acc.get("nickname", "") or "").strip()
            followers = int(acc.get("followers", 0))

            if not sec or len(sec) < 10:
                continue

            # 检查是否已是财经账号
            is_fin, conf = self._is_finance_account(name, followers)

            # 添加到账号池（如不在池中）
            pool = AccountPool.load()
            existing = None
            for pname, pinfo in pool.items():
                if pinfo.get("sec_uid", "") == sec:
                    existing = pname
                    break

            # ★ 只添加财经相关账号（必须通过账号名或内容验证）
            if not existing and is_fin and conf >= 0.7:
                display_name = name or f"account_{sec[:8]}"
                pool[display_name] = {
                    "account_id": "",
                    "sec_uid": sec,
                    "follower_count": max(followers, MIN_FOLLOWER_THRESHOLD),
                    "level": "top1000" if followers >= 500000 else "10w_plus",
                    "first_seen": datetime.now().strftime("%Y-%m-%d"),
                    "post_count": 0,
                    "is_finance_verified": True,
                    "finance_score": conf,
                    "finance_post_count": 1,
                    "last_scraped_date": "",
                }
                discovered += 1

        if discovered > 0:
            AccountPool.save(pool)
            logger.info("  [自动发现] 从 '%s' 主页发现 %d 个新账号, 池总数: %d",
                       source_account[:20], discovered, len(pool))
        return discovered

    async def _scrape_worker_async(
        self, accounts: List[tuple], worker_id: int,
    ) -> List[Dict]:
        """
        单个worker的异步采集逻辑（被多线程包装）。
        每个线程内创建独立的 browser → context → page。
        ★ v7: 每个worker使用随机UA + 随机viewport防指纹关联
        """
        has_login = os.path.exists(STORAGE_STATE)
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=self.headless,
            args=LAUNCH_ARGS,
        )
        # ★ v7: 随机化指纹
        worker_vp = get_random_viewport()
        worker_ua = get_random_ua()
        context = await browser.new_context(
            viewport=worker_vp,
            locale="zh-CN", timezone_id="Asia/Shanghai",
            user_agent=worker_ua,
            storage_state=STORAGE_STATE if has_login else None,
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "Cache-Control": "max-age=0",
                "sec-ch-ua": get_random_sec_ch_ua(),
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )
        await context.add_init_script(STEALTH_INIT_SCRIPT)

        page = await context.new_page()
        # ★ 屏蔽图片/CSS/字体/媒体，加速页面加载
        await self._block_unnecessary_resources(page)
        xhr_data: List[Dict] = []
        # ★ v5: 使用宽泛handler(不限API白名单) + chunked编码已修复
        page.on("response", self._make_broad_response_handler(xhr_data))

        results = []
        cutoff_time = datetime.now() - timedelta(hours=POST_MAX_AGE_HOURS)
        batch_size = len(accounts)

        for idx, (acc_name, sec_uid, followers, priority) in enumerate(accounts):
            xhr_data.clear()
            # ★ v5: 微延迟防限流
            await asyncio.sleep(random.uniform(WORKER_DELAY_MIN_S, WORKER_DELAY_MAX_S))

            account_success = False  # ★ v5: 跟踪是否采集成功
            try:
                await page.goto(
                    f"https://www.douyin.com/user/{sec_uid}",
                    wait_until="domcontentloaded",
                    timeout=PROFILE_PAGE_TIMEOUT_S * 1000,
                )
                # ★ v8: 检测验证码/登录页面
                page_title = await page.title()
                captcha_keywords = ["验证", "登录", "验证码", "滑块", "captcha", "login", "verify"]
                if any(kw in page_title.lower() for kw in captcha_keywords):
                    logger.warning("  [T%d] ⚠️ 疑似验证码/登录页面，Cookie可能已过期 (标题: %s)",
                                   worker_id, page_title[:50])
                # ★ v5: 等待RENDER_DATA元素出现
                try:
                    await page.wait_for_selector(
                        "#RENDER_DATA",
                        timeout=PROFILE_SSR_WAIT_S * 1000,
                    )
                except Exception:
                    pass  # SSR不存在也不影响，继续XHR方式

                # ★ v5: SSR提取
                raw_posts = await self._extract_from_ssr(page)
                if raw_posts:
                    posts = self._parse(raw_posts, acc_name)
                    if posts:
                        account_success = True
                else:
                    posts = []

                # ★ v5: SSR失败/不足 → 充分滚动+XHR
                if not posts or len(posts) < 3:
                    for _ in range(PROFILE_SCROLL_ROUNDS):
                        await page.evaluate(
                            "window.scrollTo(0, document.body.scrollHeight)"
                        )
                        await asyncio.sleep(PROFILE_SCROLL_INTERVAL_S)
                    # 等待XHR回调完成
                    await asyncio.sleep(PROFILE_XHR_WAIT_S)
                    xhr_posts = self._extract_posts_from_xhr_broad(xhr_data, acc_name)
                    if xhr_posts:
                        existing_ids = {p.get("post_id", "") for p in posts}
                        new_posts = [p for p in xhr_posts
                                    if p.get("post_id", "") not in existing_ids]
                        posts.extend(new_posts)
                        if new_posts:
                            account_success = True

                # XHR也失败 → DOM兜底
                if not posts:
                    posts = await self._extract_from_dom(page, acc_name)
                    if posts:
                        account_success = True

                # 验证 + 去重 + 时间过滤
                for p in posts:
                    if not self._validate_post(p):
                        continue
                    pid = p.get("post_id", "")
                    if not pid or pid in self._seen_ids:
                        continue
                    pt = p.get("post_time")
                    if pt and isinstance(pt, datetime) and pt < cutoff_time:
                        continue

                    p["_is_finance_account"] = True
                    p["_account_finance_confidence"] = 0.85
                    p["account_name"] = acc_name
                    p["sec_uid"] = sec_uid
                    p["account_level"] = (
                        "top1000" if followers >= 500000 else "10w_plus"
                    )
                    p["follower_count"] = followers

                    if not self._is_finance_related(
                        content=p.get("post_content", ""),
                        account_name=acc_name,
                        is_finance_account=True,
                        account_confidence=0.85,
                    ):
                        continue

                    results.append(p)

            except asyncio.TimeoutError:
                logger.debug("  [T%d] '%s' 超时(跳过, 不标记已采集)",
                           worker_id, acc_name[:20])
            except Exception as e:
                logger.debug("  [T%d] '%s' 异常: %s",
                           worker_id, acc_name[:20], str(e)[:60])

            # ★ v5: 只有成功采集到的账号才标记为今日已采集
            # 失败账号保留在池中，下次可重试
            if account_success:
                try:
                    await AccountPool.mark_scraped_today_async(acc_name)
                except Exception:
                    pass

            # ★ v5: 自动发现（每3个账号提取推荐账号）
            if (idx + 1) % 3 == 0:
                try:
                    await self._discover_from_profile(page, acc_name)
                except Exception:
                    pass

            # ★ v5: 批次休息（防限流）
            if (idx + 1) % PROFILE_BATCH_REST_EVERY == 0:
                rest = random.uniform(PROFILE_BATCH_REST_MIN_S, PROFILE_BATCH_REST_MAX_S)
                logger.info("  [T%d] %d/%d accounts, %d posts | 休息 %.1fs",
                           worker_id, idx + 1, batch_size, len(results), rest)
                await asyncio.sleep(rest)
            elif (idx + 1) % 30 == 0:
                logger.info("  [T%d] %d/%d accounts, %d posts",
                           worker_id, idx + 1, batch_size, len(results))

        await page.close()
        await context.close()
        await browser.close()
        await pw.stop()
        return results

    def _scrape_worker_sync(
        self, accounts: List[tuple], worker_id: int,
    ) -> List[Dict]:
        """
        同步包装器 —— 在独立线程中运行 asyncio 事件循环。
        每个线程创建独立的 event loop + browser，真正的并行 I/O。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self._scrape_worker_async(accounts, worker_id)
            )
        finally:
            loop.close()

    async def _scrape_profiles_multithread(
        self, accounts: List[tuple], max_workers: int = 10,
    ) -> int:
        """
        ★ v3.1: ThreadPoolExecutor 真多线程并发采集。
        每个线程 = 独立浏览器 + 独立事件循环，真正的并行 I/O。

        Args:
            accounts: [(name, sec_uid, followers, priority), ...]
            max_workers: 线程数（= 并发浏览器数）

        Returns:
            新增帖子总数
        """
        if not accounts:
            return 0

        # 均匀分配账号到各线程
        batches = [[] for _ in range(max_workers)]
        for i, acc in enumerate(accounts):
            batches[i % max_workers].append(acc)
        active_batches = [(i, b) for i, b in enumerate(batches) if b]

        logger.info("  ★ 多线程采集: %d 账号 → %d 线程 (每线程 %d~%d 账号)",
                     len(accounts), len(active_batches),
                     min(len(b) for _, b in active_batches),
                     max(len(b) for _, b in active_batches))

        t_start = datetime.now()
        seen_lock = threading.Lock()
        all_results = []

        # 在线程池中并行执行
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._scrape_worker_sync, batch, wid
                ): wid
                for wid, batch in active_batches
            }

            for future in as_completed(futures):
                wid = futures[future]
                try:
                    batch_results = future.result()
                    # 线程安全合并
                    with seen_lock:
                        for p in batch_results:
                            pid = p.get("post_id", "")
                            if pid not in self._seen_ids:
                                self._seen_ids.add(pid)
                                all_results.append(p)
                except Exception as e:
                    logger.error("  [T%d] 线程异常: %s", wid, str(e)[:100])

        self._posts.extend(all_results)
        elapsed = (datetime.now() - t_start).total_seconds()
        logger.info("  ✅ 多线程采集完成: %d 帖 | %.1f 秒 (%.0f 帖/秒)",
                     len(all_results), elapsed,
                     len(all_results) / elapsed if elapsed > 0 else 0)
        return len(all_results)

    # ==================== 财经账号 & 内容过滤 ====================

    @staticmethod
    def _is_finance_account(account_name: str, follower_count: int = 0
                            ) -> tuple:
        """
        根据账号名判断是否是财经账号。

        Returns:
            (is_finance: bool, confidence: float)
            confidence: 0.0~1.0，越高越确定是财经号
        """
        name = account_name.strip()
        if not name or name == "未知":
            return (False, 0.0)

        # 1. 黑名单检测 —— 命中则直接排除
        for bk in FINANCE_ACCOUNT_BLACKLIST:
            if bk in name:
                logger.debug("  [账号-黑名单] '%s' 命中黑名单 '%s'", name, bk)
                return (False, 0.0)

        # 2. 财经关键词匹配
        best_confidence = 0.0
        matched_kw = ""
        for kw, conf in FINANCE_ACCOUNT_KEYWORDS:
            if kw in name:
                if conf > best_confidence:
                    best_confidence = conf
                    matched_kw = kw

        if best_confidence >= 0.7:
            logger.debug("  [账号-财经] '%s' 命中 '%s' (置信度 %.2f)", name, matched_kw, best_confidence)
            return (True, best_confidence)

        # 3. 中置信度：粉丝辅助判断
        if best_confidence >= 0.4:
            if follower_count >= 100000:
                best_confidence = min(best_confidence + 0.15, 0.85)
                logger.debug("  [账号-财经+] '%s' 命中 '%s' (置信度 %.2f, 粉丝 %d)",
                            name, matched_kw, best_confidence, follower_count)
                return (True, best_confidence)
            else:
                logger.debug("  [账号-弱信号] '%s' 命中 '%s' (置信度 %.2f, 粉丝不足)",
                            name, matched_kw, best_confidence)
                return (False, best_confidence)

        # 4. 检查 AccountPool 中是否有记录
        pool_info = AccountPool.lookup(name)
        if pool_info and pool_info.get("is_finance_verified"):
            logger.debug("  [账号-池验证] '%s' 在已验证池中", name)
            return (True, 0.80)

        # 5. 无匹配
        logger.debug("  [账号-非财经] '%s' 无财经特征", name)
        return (False, 0.0)

    def _is_finance_related(self, content: str, account_name: str = "",
                            is_finance_account: bool = False,
                            account_confidence: float = 0.0) -> bool:
        """
        判断帖子是否与财经相关（账号+内容双重检测）。

        分层策略:
          - 财经账号 + 高置信度(≥0.7): 跳过内容检查，直接通过
          - 财经账号 + 中置信度(0.4~0.7): 内容只需命中 1 个财经关键词
          - 非财经账号: 内容需命中 2+ 财经关键词 + 长度 > 50 字
          - 噪声/广告关键词: 所有层级都拒绝
        """
        content = str(content)

        # === 噪声/广告检测（所有层级生效）===
        # FILTER_KEYWORDS（广告词）
        for nk in FILTER_KEYWORDS:
            if nk in content:
                logger.debug("  [过滤-广告] 命中 '%s': %.60s", nk, content)
                return False
        # NOISE_KEYWORDS（非财经噪声）
        for nk in NOISE_KEYWORDS:
            if nk in content:
                logger.debug("  [过滤-噪声] 命中 '%s': %.60s", nk, content)
                return False

        # === 内容财经关键词命中统计 ===
        hit_count = 0
        strong_hit_count = 0
        hit_words = []
        for kw in FINANCE_RELEVANCE_KEYWORDS:
            if kw in content:
                hit_count += 1
                hit_words.append(kw)
        # 强信号词统计（单独统计，不提前退出）
        for kw in STRONG_FINANCE_INDICATORS:
            if kw in content:
                strong_hit_count += 1

        # === 分层判断 ===
        # 层级1: 高置信财经账号 → 直接通过
        if is_finance_account and account_confidence >= 0.7:
            if hit_count == 0:
                logger.debug("  [过滤-账号信任] '%s' 置信度%.2f, 无关键词但信任账号",
                           account_name, account_confidence)
            return True

        # 层级2: 中置信财经账号 → 宽松（1个关键词即可）
        if is_finance_account and account_confidence >= 0.4:
            if hit_count >= 1:
                return True
            logger.debug("  [过滤-账号弱] '%s' 置信度%.2f, 但内容无财经关键词: %.60s",
                       account_name, account_confidence, content)
            return False

        # 层级3: 非财经账号 → ★v5适度放宽★
        #   1个强信号词 或 ≥3个普通财经词 即可通过
        if strong_hit_count >= 1:
            logger.debug("  [过滤-非财经强信号] %d个强信号词: %.80s", strong_hit_count, content)
            return True
        if hit_count >= 3:
            logger.debug("  [过滤-非财经普通] %d个财经词: %.80s", hit_count, content)
            return True

        # 层级4: 不通过
        logger.debug("  [过滤-不通过] 账号='%s' 强信号=%d 总词=%d: %.60s",
                   account_name, strong_hit_count, hit_count, content)
        return False

    def _to_df(self) -> pd.DataFrame:
        if not self._posts:
            return pd.DataFrame(columns=[
                "post_id", "platform", "account_id", "account_name", "account_level",
                "follower_count", "sec_uid", "post_type", "post_content", "post_url",
                "like_count", "comment_count", "share_count", "collect_count",
                "post_time", "collect_time", "search_keyword",
                "_is_finance_account", "_account_finance_confidence",
            ])
        df = pd.DataFrame(self._posts)
        # ★ 安全检查：确保关键列存在
        for col in ["post_content", "account_name", "account_level", "post_type",
                     "post_time", "post_id", "follower_count"]:
            if col not in df.columns:
                df[col] = ""
        total_before = len(df)

        # 过滤1: 广告/诈骗帖子（FILTER_KEYWORDS）
        mask_ad = df["post_content"].apply(lambda c: any(kw in str(c) for kw in FILTER_KEYWORDS))
        df["is_ad"] = mask_ad.astype(int)
        df = df[df["is_ad"] == 0].copy()
        dropped_ad = total_before - len(df)
        if dropped_ad > 0:
            logger.info("  [_to_df] 广告过滤: -%d 条 (剩余 %d)", dropped_ad, len(df))

        # ★ 财经过滤已在 run() 中执行，这里不再重复

        # 过滤2: 时间窗口 —— 保留最近POST_MAX_AGE_HOURS小时内的帖子
        time_threshold = datetime.now() - timedelta(hours=POST_MAX_AGE_HOURS)
        before_time = len(df)
        # ★ 确保 post_time 是 datetime 类型（CSV 恢复后可能变成 str）
        df["post_time"] = pd.to_datetime(df["post_time"], errors="coerce")
        df["post_time"] = df["post_time"].fillna(datetime.now())
        df = df[df["post_time"] >= time_threshold].copy()
        dropped_time = before_time - len(df)
        if dropped_time > 0:
            logger.info("  [_to_df] 时间过滤(%dh): -%d 条 (剩余 %d)", POST_MAX_AGE_HOURS, dropped_time, len(df))

        # ★ 安全阀：根据采集情况智能放宽时间窗口
        if len(df) == 0 and total_before > 0:
            logger.warning("  [_to_df] 所有帖子被%dh过滤! 放宽到%dh...",
                         POST_MAX_AGE_HOURS, POST_MAX_AGE_HOURS_LOOSE)
            time_threshold_loose = datetime.now() - timedelta(hours=POST_MAX_AGE_HOURS_LOOSE)
            df = pd.DataFrame(self._posts)
            df["post_time"] = pd.to_datetime(df["post_time"], errors="coerce").fillna(datetime.now())
            df = df[df["post_time"] >= time_threshold_loose].copy()
            logger.info("  [_to_df] 放宽后: %d 条 (窗口=%dh)", len(df), POST_MAX_AGE_HOURS_LOOSE)

        # ★ 修复: 移除无限制兜底——超过48h的旧帖对预测无价值，宁缺毋滥
        if len(df) == 0 and total_before > 0:
            logger.warning("  [_to_df] 放宽到%dh后仍为0条! 跳过本次预测(无时效性帖子可用)",
                         POST_MAX_AGE_HOURS_LOOSE)
            # 返回空DataFrame但不丢弃原始数据，仅表示本次无有效预测素材
            df = pd.DataFrame()

        # ★ 数据清洗 ★
        before_clean = len(df)

        # 清洗1: 去除内容过短的帖子（< 10 字无分析价值）
        mask_short = df["post_content"].str.len() < 10
        df = df[~mask_short].copy()
        dropped_short = before_clean - len(df)
        if dropped_short > 0:
            logger.info("  [_to_df] 短内容过滤(<10字): -%d 条", dropped_short)

        # 清洗2: 去除纯 emoji/符号帖（中文字符占比 < 10%）
        import re as _re
        def _chinese_ratio(text: str) -> float:
            text = str(text)
            if len(text) == 0:
                return 0.0
            chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
            return chinese_chars / len(text)
        mask_chinese = df["post_content"].apply(lambda c: _chinese_ratio(c) >= 0.10)
        df = df[mask_chinese].copy()
        dropped_emoji = before_clean - dropped_short - len(df)
        if dropped_emoji > 0:
            logger.info("  [_to_df] 非中文内容过滤: -%d 条", dropped_emoji)

        # 清洗3: 去除全为 #话题 的帖子（无实质内容）
        mask_tags = df["post_content"].apply(
            lambda c: len(_re.sub(r'#\S+', '', str(c)).strip()) >= 10
        )
        df = df[mask_tags].copy()
        dropped_tags = before_clean - dropped_short - dropped_emoji - len(df)
        if dropped_tags > 0:
            logger.info("  [_to_df] 纯话题帖过滤: -%d 条", dropped_tags)

        df["is_stock_only"] = 0
        df.reset_index(drop=True, inplace=True)

        # ★ 统计各维度过滤数据
        account_levels = df["account_level"].value_counts().to_dict() if len(df) > 0 else {}
        post_types = df["post_type"].value_counts().to_dict() if len(df) > 0 else {}
        logger.info("  [_to_df] 最终输出: %d 条 (原始 %d 条) | top1000=%d, 10w_plus=%d | 视频=%d, 图文=%d",
                   len(df), total_before,
                   account_levels.get("top1000", 0),
                   account_levels.get("10w_plus", 0),
                   post_types.get("video", 0),
                   post_types.get("image_text", 0))
        return df

    # ==================== 检查登录态 ====================

    def _check_login_state(self) -> bool:
        """检查 storage_state 是否有效，缺失关键cookie时明确报错"""
        if not os.path.exists(STORAGE_STATE):
            logger.error("=" * 60)
            logger.error("❌ 未找到登录态文件: %s", STORAGE_STATE)
            logger.error("   请先执行: python module1_account_collector.py --login")
            logger.error("=" * 60)
            return False

        try:
            with open(STORAGE_STATE, "r", encoding="utf-8") as f:
                state = json.load(f)

            cookies = state.get("cookies", [])
            if not cookies:
                logger.error("=" * 60)
                logger.error("❌ 登录态文件中无 cookies！")
                logger.error("   请重新执行: python module1_account_collector.py --login")
                logger.error("=" * 60)
                return False

            # 检查是否有 douyin.com 的 cookie
            dy_cookies = [c for c in cookies if "douyin" in c.get("domain", "")]
            if not dy_cookies:
                logger.error("=" * 60)
                logger.error("❌ 登录态中未找到 douyin.com 域名 cookies！")
                logger.error("   请重新执行: python module1_account_collector.py --login")
                logger.error("=" * 60)
                return False

            cookie_names = {c.get("name", "") for c in dy_cookies}
            required_groups = [
                (["passport_csrf_token", "sid_guard", "uid_tt", "sessionid"],
                 "登录核心", "❌"),
                (["msToken", "ttwid", "odin_tt"],
                 "风控令牌", "⚠️"),
                (["webid", "install_id"],
                 "设备标识", "⚡"),
            ]
            all_ok = True
            critical_missing = False
            for keys, label, icon in required_groups:
                missing = [k for k in keys if k not in cookie_names]
                if missing:
                    if icon == "❌":
                        logger.error("  %s %s缺失: %s → 登录已过期！", icon, label, missing)
                        all_ok = False
                        critical_missing = True
                    else:
                        logger.warning("  %s %s缺失: %s → 可能影响风控校验", icon, label, missing)

            if critical_missing:
                logger.error("=" * 60)
                logger.error("❌ 登录核心cookie缺失，登录已过期！")
                logger.error("   请重新执行: python module1_account_collector.py --login")
                logger.error("=" * 60)
                return False

            # ★ v5: 自动生成缺失的风控/设备令牌（避免搜索返回空结果）
            if not all_ok:
                import secrets
                auto_gen = {
                    "msToken": secrets.token_hex(64)[:107],
                    "webid": secrets.token_hex(16)[:32],
                    "install_id": secrets.token_hex(16)[:32],
                }
                patched = []
                for name, value in auto_gen.items():
                    if name not in cookie_names:
                        state["cookies"].append({
                            "name": name, "value": value,
                            "domain": ".douyin.com", "path": "/",
                            "httpOnly": False, "secure": True, "sameSite": "Lax",
                        })
                        patched.append(name)
                if patched:
                    with open(STORAGE_STATE, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)
                    logger.info("  🔧 自动生成缺失令牌: %s", patched)
                    all_ok = True

            logger.info("  ✅ Cookie完整 (%d个域名cookies)", len(dy_cookies))
            return True

        except Exception as e:
            logger.error("❌ 登录态文件读取失败: %s", str(e))
            return False

    # ==================== 种子账号导入 ====================

    SEED_FILE = os.path.join(OUTPUT_DIR, "dy_seed_accounts.json")

    def _import_seed_accounts(self):
        """★ v5: 导入种子财经账号到账号池（预置高质量起点）"""
        if not os.path.exists(self.SEED_FILE):
            logger.info("  [种子] 未找到种子文件, 跳过: %s", self.SEED_FILE)
            return 0

        try:
            with open(self.SEED_FILE, "r", encoding="utf-8") as f:
                seeds = json.load(f)
        except Exception as e:
            logger.warning("  [种子] 文件读取失败: %s", str(e))
            return 0

        pool = AccountPool.load()
        imported = 0
        updated = 0

        for s in seeds:
            name = str(s.get("name", "") or "").strip()
            sec = str(s.get("sec_uid", "") or "").strip()
            if not name:
                continue
            # ★ sec_uid可以为空(后续搜索发现会自动填充)

            followers = int(s.get("followers", MIN_FOLLOWER_THRESHOLD))
            note = str(s.get("note", ""))

            if name in pool:
                # 更新已有账号的 sec_uid 和粉丝数（种子数据通常更准）
                existing = pool[name]
                if not existing.get("sec_uid"):
                    existing["sec_uid"] = sec
                    updated += 1
                if existing.get("follower_count", 0) < followers:
                    existing["follower_count"] = followers
                if not existing.get("is_finance_verified"):
                    existing["is_finance_verified"] = True
                    existing["finance_score"] = 0.95
                    existing["finance_post_count"] = max(existing.get("finance_post_count", 0), 5)
                if existing.get("level", "") not in ("top1000", "10w_plus"):
                    existing["level"] = "top1000" if followers >= 500000 else "10w_plus"
            else:
                # 新账号
                pool[name] = {
                    "account_id": "",
                    "sec_uid": sec,
                    "follower_count": followers,
                    "level": "top1000" if followers >= 500000 else "10w_plus",
                    "first_seen": datetime.now().strftime("%Y-%m-%d"),
                    "post_count": 0,
                    "is_finance_verified": True,
                    "finance_score": 0.95,
                    "finance_post_count": 5,
                    "last_scraped_date": "",
                    "_seed_note": note,
                }
                imported += 1

        if imported > 0 or updated > 0:
            AccountPool.save(pool)
            logger.info("  [种子] ✅ 导入 %d 新账号, 更新 %d 已有账号 | 池总数: %d",
                       imported, updated, len(pool))
        else:
            logger.info("  [种子] 所有种子账号已在池中，无需导入")

        return imported

    # ==================== 主入口 ====================

    async def run(self) -> pd.DataFrame:
        """
        v3 采集流程（精准 + 高速）:
          Phase 0: 财经账号发现（扩充账号池）
          Phase 1: ★ 多Context并发采集财经账号主页（10 worker并行）
          移除: 关键词搜索阶段（噪声来源）
        """
        t_start = datetime.now()
        self._validate_account_pool()
        has_login = self._check_login_state()
        self._import_seed_accounts()  # ★ v5: 导入种子账号

        logger.info("=" * 60)
        logger.info("抖音财经采集 v4.2 | 高速并发 | headless=%s | 登录态=%s",
                     self.headless, "有效" if has_login else "❌缺失")
        logger.info("目标: 前%d名财经自媒体 + 粉丝>10万 | 24h全部帖文(视频+图文) | %d线程",
                     COLLECTION_TOP_N, MAX_CONCURRENT_CONTEXTS)
        logger.info("=" * 60)

        if not has_login:
            logger.error("=" * 60)
            logger.error("❌ 未登录或登录态不完整，采集将无法获取数据！")
            logger.error("  抖音搜索API需要完整的cookie才能返回结果。")
            logger.error("  请立即执行以下命令完成登录:")
            logger.error("")
            logger.error("      python module1_account_collector.py --login")
            logger.error("")
            logger.error("  登录后在浏览器中多浏览几个页面（≥30秒）")
            logger.error("  让抖音设置完整的cookie后再关闭浏览器窗口")
            logger.error("=" * 60)
            # ★ v5: 不做sys.exit，允许尽力而为的采集（主页可能还能访问）

        pw = await async_playwright().start()

        browser = await pw.chromium.launch(
            headless=self.headless,
            args=LAUNCH_ARGS,
        )

        try:
            # ============================================================
            # Phase 0: 财经账号发现（账号池充足则直接跳过）
            # ============================================================
            pool = AccountPool.load()
            pool_before = len(pool)
            # 统计有 sec_uid 且未采集的账号数
            today_str = datetime.now().strftime("%Y-%m-%d")
            ready = sum(1 for i in pool.values()
                       if str(i.get("sec_uid","") or "").strip()
                       and i.get("last_scraped_date") != today_str)

            logger.info("=" * 50)
            logger.info("Phase 0: 搜索采集+账号发现 | 账号池=%d | 待采集=%d", pool_before, ready)
            logger.info("=" * 50)

            # ★ FAST-PATH: 账号池充足(>=800)直接跳过发现阶段
            if pool_before >= 800:
                logger.info("  ⏭ 账号池已充足(%d个)，跳过Phase 0（快速模式）", pool_before)
            else:
                # Phase 0: 仅账号池不足时执行搜索发现
                search_vp = get_random_viewport()
                search_ua = get_random_ua()
                discovery_ctx = await browser.new_context(
                    viewport=search_vp,
                    locale="zh-CN", timezone_id="Asia/Shanghai",
                    user_agent=search_ua,
                    storage_state=STORAGE_STATE if has_login else None,
                    extra_http_headers={
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        "sec-ch-ua": get_random_sec_ch_ua(),
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"',
                    },
                )
                await discovery_ctx.add_init_script(STEALTH_INIT_SCRIPT)
                discovery_page = await discovery_ctx.new_page()
                await self._block_unnecessary_resources(discovery_page)
                discovery_xhr: List[Dict] = []
                discovery_page.on("response", self._make_response_handler(discovery_xhr))
                try:
                    await discovery_page.goto("https://www.douyin.com",
                                              wait_until="domcontentloaded",
                                              timeout=TIMEOUT_PAGE_GOTO * 1000)
                    await asyncio.sleep(1)
                    page_title = await discovery_page.title()
                    if "验证" not in page_title and "captcha" not in page_title.lower():
                        await self._discover_finance_accounts_expanded(discovery_page, discovery_xhr)
                except Exception as e:
                    logger.warning("  搜索发现异常，跳过: %s", str(e)[:80])
                await discovery_page.close()
                await discovery_ctx.close()

            pool_after = len(AccountPool.load())
            logger.info("  账号池: %d → %d (+%d) | 帖子累计: %d",
                         pool_before, pool_after, pool_after - pool_before,
                         len(self._posts))

            # ============================================================
            # Phase 1: ★ 多Context并发采集财经账号主页
            # ============================================================
            candidates = self._get_scraping_candidates(
                limit=MAX_ACCOUNTS_TO_SCRAPE
            )
            logger.info("=" * 50)
            logger.info("Phase 1: ★多线程采集★ %d 个财经账号主页", len(candidates))
            logger.info("=" * 50)

            # 关闭共享浏览器（多线程各自创建独立的浏览器）
            await browser.close()
            await pw.stop()

            if candidates:
                await self._scrape_profiles_multithread(
                    candidates,
                    max_workers=MAX_CONCURRENT_CONTEXTS,
                )
            else:
                logger.warning("无待采集的财经账号（可能今天已全部采集）")

        finally:
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await pw.stop()
            except Exception:
                pass

        # ===== 收尾 =====
        self._save_resume()
        if self._posts:
            AccountPool.update(self._posts)
            self._save_to_db()

        # ★ v5: 导出每日采集数据到独立CSV文件
        df_result = self._to_df()
        if len(df_result) > 0:
            daily_csv = os.path.join(
                OUTPUT_DIR,
                f"{datetime.now().strftime('%Y%m%d')}_douyin_posts.csv",
            )
            df_result.to_csv(daily_csv, index=False, encoding="utf-8-sig")
            logger.info("  [每日CSV] ✅ 已导出: %s (%d 条)", daily_csv, len(df_result))
        else:
            # 即使0条也导出一个空文件（含表头）作为记录
            daily_csv = os.path.join(
                OUTPUT_DIR,
                f"{datetime.now().strftime('%Y%m%d')}_douyin_posts.csv",
            )
            df_result.to_csv(daily_csv, index=False, encoding="utf-8-sig")
            logger.warning("  [每日CSV] ⚠️ 空数据导出: %s", daily_csv)

        elapsed = (datetime.now() - t_start).total_seconds()
        logger.info("=" * 60)
        logger.info("采集完成: %d 条 | 耗时 %.1f 秒 | 速率 %.1f 条/秒",
                     len(self._posts), elapsed,
                     len(self._posts) / elapsed if elapsed > 0 else 0)
        if not self._posts:
            logger.warning("⚠️  0条结果！排查建议:")
            logger.warning("  1. python module1_account_collector.py --login 重新登录")
            logger.warning("  2. 检查账号池: output/dy_account_pool.json")
            logger.warning("  3. 查看 debug_screenshots/ 截图")
        logger.info("=" * 60)
        return df_result  # ★ v5: 复用已生成的结果


# ==================== 登录（改进版） ====================

async def douyin_login():
    """打开可见浏览器，给用户 120 秒扫码/手机登录，然后自动浏览页面生成完整Cookie"""
    print("\n" + "=" * 60)
    print("  抖音登录助手 v5")
    print("=" * 60)
    print("即将打开浏览器，请: ")
    print("  1. 在打开的浏览器中点击右上角「登录」")
    print("  2. 使用抖音APP扫码登录")
    print("  3. 登录成功后等待倒计时结束（会自动浏览页面生成令牌）")
    print("=" * 60 + "\n")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,
        args=["--no-sandbox", "--window-size=1280,900",
              "--disable-blink-features=AutomationControlled",
              "--exclude-switches=enable-automation"],
    )
    login_ua = get_random_ua()
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="zh-CN",
        user_agent=login_ua,
    )
    page = await ctx.new_page()
    await page.goto("https://www.douyin.com", wait_until="domcontentloaded")

    print("请扫码登录，倒计时 120 秒...")
    print("提示: 登录后请随意浏览几个视频和搜索，帮助生成完整Cookie")
    for i in range(120, 0, -1):
        if i % 20 == 0:
            print(f"\n  剩余 {i} 秒...")
        sys.stdout.write("."); sys.stdout.flush()
        await asyncio.sleep(1)
    print()

    # ★ v5: 自动浏览页面生成风控令牌（msToken/webid/install_id 等）
    print("\n正在自动浏览页面以生成风控令牌...")
    browse_urls = [
        "https://www.douyin.com",
        "https://www.douyin.com/search/A%E8%82%A1?type=general",
        "https://www.douyin.com/user/self",
    ]
    for url in browse_urls:
        try:
            print(f"  浏览: {url[:60]}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            # 滚动触发JS执行
            await page.evaluate("window.scrollTo(0, 300)")
            await asyncio.sleep(1)
        except Exception as e:
            print(f"  跳过: {str(e)[:50]}")

    print("  浏览完成，保存Cookie...")
    os.makedirs(os.path.dirname(STORAGE_STATE), exist_ok=True)
    await ctx.storage_state(path=STORAGE_STATE)

    # ★ 验证生成的Cookie是否完整，缺失的自动生成
    with open(STORAGE_STATE, "r", encoding="utf-8") as f:
        state = json.load(f)
    dy_cookies = [c for c in state.get("cookies", []) if "douyin" in c.get("domain", "")]
    names = {c.get("name") for c in dy_cookies}
    required = ["msToken", "webid", "install_id", "ttwid", "odin_tt",
                "passport_csrf_token", "sessionid"]

    # ★ v5: 自动生成缺失的风控令牌
    import secrets
    auto_gen_map = {
        "msToken": secrets.token_hex(64)[:107],       # msToken: ~107字符随机hex
        "webid": secrets.token_hex(16)[:32],           # webid: 短随机标识
        "install_id": secrets.token_hex(16)[:32],       # install_id: 短随机标识
    }
    generated = []
    for name in auto_gen_map:
        if name not in names:
            state["cookies"].append({
                "name": name,
                "value": auto_gen_map[name],
                "domain": ".douyin.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            })
            generated.append(name)

    if generated:
        with open(STORAGE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"  自动生成了缺失令牌: {generated}")

    missing = [n for n in required if n not in names and n not in generated]

    print(f"\n{'='*60}")
    if missing:
        print(f"[WARN] 仍缺失令牌: {missing}")
        print(f"  请重新运行 --login，登录后务必多浏览几个页面再关闭")
    else:
        print(f"[OK] 所有风控令牌齐全!")
        if generated:
            print(f"  (其中 {len(generated)} 个为自动生成)")
    print(f"  Cookie已保存至: {STORAGE_STATE}")
    print(f"{'='*60}")

    await ctx.close()
    await browser.close()
    await pw.stop()


def export_ranked_accounts(limit: int = None) -> str:
    """
    ★ v6: 导出抖音前1000名财经账号排名
    按粉丝数降序排列，保存到 output/dy_ranked_top1000.json
    """
    if limit is None:
        limit = COLLECTION_TOP_N
    from config import RANKED_ACCOUNTS_FILE

    pool = AccountPool.load()
    accounts = []
    for name, info in pool.items():
        if info.get("is_finance_verified") or info.get("finance_score", 0) >= 0.4:
            accounts.append({
                "account_name": name,
                "sec_uid": info.get("sec_uid", ""),
                "follower_count": info.get("follower_count", 0),
                "level": info.get("level", ""),
                "finance_score": info.get("finance_score", 0),
                "finance_post_count": info.get("finance_post_count", 0),
                "is_finance_verified": info.get("is_finance_verified", False),
                "last_scraped_date": info.get("last_scraped_date", ""),
                "platform": "douyin",
            })

    # 按粉丝数严格降序排列
    accounts.sort(key=lambda x: x["follower_count"], reverse=True)
    top_n = accounts[:limit]

    output = {
        "platform": "douyin",
        "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_accounts": len(accounts),
        "top_n": limit,
        "ranked_accounts": [],
    }

    for rank, acc in enumerate(top_n, 1):
        output["ranked_accounts"].append({
            "rank": rank,
            "account_name": acc["account_name"],
            "sec_uid": acc["sec_uid"],
            "follower_count": acc["follower_count"],
            "level": acc["level"],
            "finance_score": acc["finance_score"],
        })

    dy_rank_file = os.path.join(OUTPUT_DIR, "dy_ranked_top1000.json")
    with open(dy_rank_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info("抖音排名已导出: %s (%d个账号, 粉丝范围: %d~%d)",
               dy_rank_file, len(top_n),
               top_n[-1]["follower_count"] if top_n else 0,
               top_n[0]["follower_count"] if top_n else 0)
    return dy_rank_file


def run_account_and_collect(force_mode: str = None) -> pd.DataFrame:
    """
    模块入口（被 pipeline 调用）
    v6: 14线程高速并发采集前1000名财经账号主页，专注24h帖子
    """
    from config import PLAYWRIGHT_HEADLESS
    result = asyncio.run(DouyinCollector(headless=PLAYWRIGHT_HEADLESS).run())
    # ★ v6: 采集完成后导出排名
    export_ranked_accounts()
    return result


# ==================== 命令行入口 ====================

# ==================== 命令行入口 ====================

if __name__ == "__main__":
    import argparse
    logging.getLogger().setLevel(logging.INFO)

    p = argparse.ArgumentParser(description="FinData v3")
    p.add_argument("--login", action="store_true", help="Login")
    p.add_argument("--visible", action="store_true", help="Visible")
    p.add_argument("--headless", action="store_true", help="Headless")
    p.add_argument("--diagnose", action="store_true", help="Diagnose")
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--test-profile", type=str, default=None, metavar="SEC_UID")
    p.add_argument("--seed-file", type=str, default=None, metavar="FILE")
    p.add_argument("--show-pool", action="store_true")
    args = p.parse_args()

    if args.login:
        asyncio.run(douyin_login())

    elif args.show_pool:
        pool = AccountPool.load()
        verified = [n for n, i in pool.items() if i.get("is_finance_verified")]
        with_sec = [n for n, i in pool.items() if str(i.get("sec_uid", "") or "").strip()]
        print("Pool:", len(pool), "verified:", len(verified), "with_sec:", len(with_sec))
        for n in verified[:50]:
            info = pool[n]
            print("  ", n[:35], "fans=", info.get("follower_count",0), "sec=", info.get("sec_uid","")[:25])

    elif args.seed_file:
        seed_path = args.seed_file
        if not os.path.exists(seed_path):
            print("File not found:", seed_path); sys.exit(1)
        imported = 0
        if seed_path.endswith(".json"):
            seeds = json.load(open(seed_path, encoding="utf-8"))
            if isinstance(seeds, dict):
                seeds = [{"name": k, **v} for k, v in seeds.items()]
        elif seed_path.endswith(".csv"):
            import csv
            seeds = list(csv.DictReader(open(seed_path, encoding="utf-8-sig")))
        else:
            print("Only json/csv supported"); sys.exit(1)
        for s in seeds:
            name = str(s.get("name", "") or "").strip()
            sec = str(s.get("sec_uid", "") or "").strip()
            if not name or not sec: continue
            followers = int(s.get("followers", s.get("follower_count", 100000)))
            pool = AccountPool.load()
            pool[name] = {"account_id": s.get("account_id", ""), "sec_uid": sec,
                "follower_count": followers, "level": "top1000" if followers >= 500000 else "10w_plus",
                "first_seen": datetime.now().strftime("%Y-%m-%d"), "post_count": 0,
                "is_finance_verified": True, "finance_score": 0.95, "finance_post_count": 5,
                "last_scraped_date": ""}
            AccountPool.save(pool); imported += 1
        print("Imported", imported, "accounts. Pool:", len(AccountPool.load()))

    elif args.test_profile:
        sec_uid = args.test_profile.strip()
        print("Test: https://www.douyin.com/user/" + sec_uid)

        async def _test():
            c = DouyinCollector(headless=False, diagnose=True)
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=False, args=["--no-sandbox", "--window-size=1280,900"])
            ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN",
                user_agent=FIXED_UA,
                storage_state=STORAGE_STATE if os.path.exists(STORAGE_STATE) else None)
            await ctx.add_init_script(STEALTH_INIT_SCRIPT)
            page = await ctx.new_page()
            xhr_data = []; page.on("response", c._make_response_handler(xhr_data))

            print("[1] Load profile...")
            await page.goto("https://www.douyin.com/user/" + sec_uid, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            print("[2] SSR extract...")
            posts = await c._extract_from_ssr(page)
            print("    SSR raw:", len(posts))
            if posts:
                parsed = c._parse(posts, "test")
                valid = [p for p in parsed if c._validate_post(p)]
                print("    valid:", len(valid))
                for vp in valid[:3]:
                    txt = str(vp.get("post_content",""))[:60]
                    print("    |--", vp.get("post_id","?"), "|", txt)

            if not posts:
                print("[3] Scroll+XHR...")
                for _ in range(8):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(0.1)  # [优化: 0.5→0.1]
                posts = c._extract_posts_from_xhr(xhr_data, "test")
                print("    XHR:", len(posts))

            if not posts:
                print("[4] DOM...")
                posts = await c._extract_from_dom(page, "test")
                print("    DOM:", len(posts))

            print("---Diagnostic---")
            print("Title:", await page.title())
            print("XHR count:", len(xhr_data))
            for i, cap in enumerate(xhr_data[:10]):
                items = cap.get("items")
                n = len(items) if isinstance(items, list) else 0
                print("  XHR[" + str(i) + "]: items=" + str(n) + " keys=" + str(cap.get("top_keys",[])[:6]))

            if not posts:
                print("!! Profile also 0! Check login/account/anti-bot")
                await c._save_debug_html(page, "test_" + sec_uid[:16])

            await page.close(); await ctx.close(); await browser.close(); await pw.stop()
            print("Total:", len(posts))
            return len(posts)

        count = asyncio.run(_test())
        sys.exit(0 if count > 0 else 1)

    else:
        use_headless = args.headless
        if args.concurrency is not None:
            globals()["MAX_CONCURRENT_CONTEXTS"] = args.concurrency
        c = DouyinCollector(headless=use_headless, diagnose=args.diagnose)
        df = asyncio.run(c.run())
        rc = len(df)
        print("Result:", rc, "posts")
        if rc > 0:
            print(df[["account_name", "post_content", "like_count"]].head(10).to_string())
            sys.exit(0)
        else:
            print("0 posts! 1.--login 2.--diagnose 3.--test-profile <sec_uid> 4.--seed-file accounts.json")
            sys.exit(1)
