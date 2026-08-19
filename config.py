# -*- coding: utf-8 -*-
"""
============================================================
每日自动财经舆情预测任务 - 全局配置文件
============================================================
包含: 数据库连接、采集参数、调度时间、情感词典等
"""

import os
from datetime import datetime

# ==================== 项目根路径 ====================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

# 自动创建目录
for _d in [OUTPUT_DIR, LOGS_DIR]:
    os.makedirs(_d, exist_ok=True)

# ==================== MySQL数据库配置 ====================
MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "123456",       # 生产环境请修改
    "database": "financial_sentiment",
    "charset": "utf8mb4",
    "connect_timeout": 10,
}

# ==================== 4大宽基指数代码映射 ====================
INDEX_CODE_MAP = {
    "000001.SH": "上证指数",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
}

# ==================== 调度时间配置 ====================
MAIN_PIPELINE_TIME = "08:00"     # ★ 每日主流水线启动时间（8点采集前24h帖子，9:30开盘前出预测）
BACKTEST_TIME = "15:35"          # 每日回测任务启动时间

# ==================== 采集配置 ====================
# ★ 每日采集前1000名财经博主（按粉丝数排序），可自定义修改
COLLECTION_TOP_N = 1000           # ★ 采集前1000名财经博主（抖音+小红书各1000名）
COLLECTION_SORT_BY = "follower_count"  # ★ 排序方式: follower_count / view_count
TOP1000_POOL_SIZE = 1200          # ★ 账号池预留1200（含缓冲）
MIN_FOLLOWER_THRESHOLD = 50000    # ★ 5万粉丝最低门槛(放宽以增加数据量)
XHS_COLLECTION_TOP_N = 1000       # ★ 小红书前1000名
XHS_MIN_FOLLOWER_THRESHOLD = 50000  # ★ 小红书5万粉丝门槛(放宽)

# 过滤关键词（纯个股推荐/理财广告相关）
FILTER_KEYWORDS = [
    "加微信", "扫码领取", "免费荐股", "牛股推荐", "跟上操作",
    "内部票", "涨停预测", "加群", "私信", "点击链接",
    "稳赚", "保本", "高收益理财", "开户送礼", "佣金万",
    "限时特惠", "名额有限", "转发领取", "朋友圈集赞",
]

# ==================== 财经相关性过滤 ====================
# ★ 只对含财经关键词的帖子做情感分析，过滤掉财经博主发的非财经内容
#   （做饭/养花/演唱会/生活vlog等），避免中性占比被无关内容拉高
FINANCE_POST_KEYWORDS = [
    # 指数/大盘
    "上证", "沪深", "中证", "创业板", "科创板", "A股", "大盘", "指数",
    "沪指", "深指", "北证", "宽基", "上证50", "沪深300", "中证500", "中证1000",
    # 行情/涨跌（用复合词，避免单个"涨/跌"误匹配"涨粉"）
    "上涨", "下跌", "大涨", "大跌", "暴涨", "暴跌", "涨停", "跌停",
    "反弹", "回调", "回落", "拉升", "跳水", "震荡", "突破", "破位",
    "牛市", "熊市", "收盘", "开盘", "行情", "复盘", "收评", "走势",
    "放量", "缩量", "冲高", "企稳", "筑底", "见顶", "多头", "空头",
    # 板块/题材
    "板块", "题材", "赛道", "龙头", "概念", "半导体", "新能源", "光伏",
    "白酒", "医药", "券商", "军工", "科技", "消费", "地产", "银行",
    "煤炭", "有色", "钢铁", "保险", "汽车", "机器人", "人工智能",
    # 资金/交易
    "北向", "主力", "资金", "流入", "流出", "成交量", "仓位", "加仓",
    "减仓", "建仓", "清仓", "买入", "卖出", "持有", "抄底", "止盈", "止损",
    "融资", "融券", "杠杆", "龙虎榜", "ETF", "etf",
    # 基本面/政策
    "利好", "利空", "政策", "降准", "降息", "加息", "业绩", "财报",
    "估值", "市盈率", "基本面", "技术面", "GDP", "宏观", "美联储",
    "央行", "利率", "汇率", "关税", "通胀",
    # 投资/交易
    "股票", "基金", "投资", "炒股", "股民", "散户", "庄家", "券商",
    "打新", "新股", "市值", "分红", "股息", "债券", "理财",
]

# ==================== Excel报表配置 ====================
EXCEL_REPORT_TEMPLATE = os.path.join(OUTPUT_DIR, "{date}_财经舆情预测日报.xlsx")

# ==================== 日志配置 ====================
LOG_FILE = os.path.join(LOGS_DIR, f"pipeline_{datetime.now().strftime('%Y%m%d')}.log")
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(module)s:%(lineno)d | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ==================== Playwright 浏览器配置 ====================
PLAYWRIGHT_HEADLESS = False     # ★ 可见浏览器模式(避免触发抖音验证码)
PLAYWRIGHT_MAX_CONCURRENT_PAGES = 2       # 并发页面数(降低避免被封)
PLAYWRIGHT_PAGE_TIMEOUT_MS = 12000        # 单页加载超时(ms) [优化: 15s→12s]
PLAYWRIGHT_BLOCKED_RESOURCE_TYPES = {     # 屏蔽以加速
    "image", "stylesheet", "font", "media", "manifest",
}
# Cookie API模式线程数
MAX_COOKIE_API_WORKERS = 8               # [优化: 4→8]

# ==================== 采集时间窗口配置 ====================
POST_MAX_AGE_HOURS = 24             # ★ 帖子最大保留时间(小时)，24小时=1天窗口（严格时效性）
POST_MAX_AGE_HOURS_LOOSE = 48       # ★ 宽松窗口(小时)，帖子不足时最多放宽到48h(2天)

# ==================== 小红书(Xiaohongshu)采集配置 ====================
XHS_STORAGE_STATE = os.path.join(OUTPUT_DIR, "xhs_storage_state.json")
XHS_RESUME_CSV = os.path.join(OUTPUT_DIR, "xhs_posts_resume.csv")
XHS_ACCOUNT_POOL_FILE = os.path.join(OUTPUT_DIR, "xhs_account_pool.json")
XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result"
XHS_USER_PROFILE_URL = "https://www.xiaohongshu.com/user/profile"
XHS_MAX_CONCURRENT_CONTEXTS = 14    # ★ 小红书并发数 [优化: 10→14]
XHS_PROFILE_SCROLL_ROUNDS = 8       # ★ 主页滚动次数 [优化: 12→8]
XHS_SEARCH_SCROLL_ROUNDS = 5        # ★ 搜索页滚动次数 [优化: 8→5]
XHS_REQUEST_DELAY_MIN = 0.3         # ★ 请求最小间隔(秒) [优化: 0.8→0.3]
XHS_REQUEST_DELAY_MAX = 1.0         # ★ 请求最大间隔(秒) [优化: 2.0→1.0]
XHS_DISCOVERY_KW_COUNT = 30         # ★ 账号发现阶段关键词数（与抖音保持一致）

# 小红书搜索关键词（财经相关）
XHS_FINANCE_KEYWORDS = [
    # 指数/大盘
    "A股复盘", "A股收评", "今日A股", "股市行情", "上证指数",
    "沪深300", "中证500", "中证1000", "创业板", "科创板",
    # 投资策略
    "基金操作", "基金定投", "ETF投资", "股票投资", "投资策略",
    "价值投资", "短线交易", "波段操作", "仓位管理",
    # 财经资讯
    "财经解读", "市场分析", "宏观经济", "行业研究",
    # 板块热点
    "新能源板块", "半导体板块", "AI板块", "白酒板块",
    "券商板块", "军工板块", "光伏板块", "医药板块",
    # 技术分析
    "技术分析", "K线分析", "成交量分析", "MACD",
    # 资金流向
    "北向资金", "主力资金", "龙虎榜", "资金流向",
]

# 小红书API端点白名单（XHR拦截用）
XHS_API_PATTERNS = [
    "/api/sns/web/v1/note/",          # 笔记详情
    "/api/sns/web/v1/user/notes",      # 用户笔记列表
    "/api/sns/web/v1/search/notes",    # 搜索笔记
    "/api/sns/web/v1/feed",            # 推荐流
    "/api/sns/web/v1/homefeed",        # 首页
    "/api/sns/web/v2/note/",           # 笔记v2
    "/api/sns/web/v1/user/info",       # 用户信息
    "/api/sns/web/v1/search/",         # 搜索相关
]

# ==================== 跨平台账号排名配置 ====================
RANKED_ACCOUNTS_FILE = os.path.join(OUTPUT_DIR, "ranked_accounts_top1000.json")
RANK_TOP_N = 1000                      # ★ 综合排名前1000名
PLATFORM_WEIGHTS = {                   # ★ 平台权重（预测用）
    "douyin": 1.0,                     # 抖音权重
    "xiaohongshu": 0.85,               # 小红书权重（图文为主，略低）
}

# ==================== LLM 大模型配置 ====================
# ★ LLM_ENABLED=True 时使用 DeepSeek API 做情感分析
# ★ LLM_ENABLED=False 时使用原有的财经关键词词典
LLM_ENABLED = True                # True=使用LLM, False=使用词典
LLM_MODEL = "deepseek-chat"        # 模型: deepseek-chat / deepseek-reasoner
LLM_MAX_TOKENS = 300               # 单次调用最大输出token
LLM_TEMPERATURE = 0.0              # 温度(0=确定性输出)
LLM_BATCH_SIZE = 20                # 批量调用每批条数(一次API调用分析N条)
LLM_CONCURRENCY = 10               # 异步并发请求数 [优化: 5→10]
LLM_MAX_RETRIES = 1                # API 调用失败重试次数 [优化: 3→1]
LLM_BASE_URL = "https://api.deepseek.com/v1"  # DeepSeek API 地址

# API Key 通过环境变量 DEEPSEEK_API_KEY 读取，不在此配置
# Windows: set DEEPSEEK_API_KEY=sk-xxxxx
# Linux/Mac: export DEEPSEEK_API_KEY=sk-xxxxx

# ==================== 视频内容提取配置 ====================
# ★ 从视频帖子中提取音频文字和画面内容，丰富情感分析输入
# ★ 视频 → ffmpeg提取音频 → Whisper语音转文字 → OpenCV抽帧 → 多模态视觉分析 → 文本合并

# 功能开关
VIDEO_EXTRACTION_ENABLED = True       # True=对视频帖子提取内容, False=跳过(仅用文字描述)

# 视频下载
VIDEO_MAX_DURATION_SECONDS = 180      # ★ 只下载/分析视频前N秒（3分钟，金融视频核心观点通常在前段）
VIDEO_DOWNLOAD_TIMEOUT = 60           # 下载超时(秒)
VIDEO_CACHE_DIR = os.path.join(OUTPUT_DIR, "video_cache")  # 临时视频文件目录
VIDEO_MAX_CACHE_SIZE_MB = 2000        # ★ 视频缓存最大占用空间(MB)，超出自动清理旧文件
VIDEO_KEEP_TEMP_FILES = False         # ★ 是否保留临时文件(调试用，生产环境建议False)

# 帧提取
VIDEO_FRAME_INTERVAL_SECONDS = 10     # ★ 每隔N秒提取一帧
VIDEO_MAX_FRAMES = 10                 # ★ 每个视频最多分析N帧
VIDEO_FRAME_JPEG_QUALITY = 70         # 帧图片JPEG压缩质量(1-100)

# 语音识别 (Whisper)
WHISPER_MODEL_SIZE = "base"           # tiny / base / small / medium / large
                                      # base(~142MB)适合中文，速度与准确度平衡
WHISPER_LANGUAGE = "zh"               # 指定中文以获得更好准确率

# Vision 代理配置
VISION_BACKEND_ORDER = ["openai", "ocr"]
                                      # ★ 按优先级排序，第一个可用的是主力
                                      # 可选值: openai / qwen_vl / ocr / paddle_ocr
                                      # 默认: openai优先，EasyOCR兜底(免费本地OCR)

# Vision - OpenAI兼容接口 (如GPT-4o，或任何兼容Vision API)
VISION_OPENAI_API_KEY = os.environ.get("VISION_OPENAI_API_KEY", "")
VISION_OPENAI_BASE_URL = "https://api.openai.com/v1"
VISION_OPENAI_MODEL = "gpt-4o"        # 或 gpt-4o-mini 节省成本
VISION_OPENAI_MAX_TOKENS = 200        # 单帧描述最大token
VISION_OPENAI_TIMEOUT = 30            # 单帧API调用超时(秒)
# 注意: VISION_OPENAI_API_KEY 独立于 DEEPSEEK_API_KEY（因为DeepSeek暂不支持Vision）
# 如果使用阿里云百炼、智谱GLM-4V等兼容接口，修改 VISION_OPENAI_BASE_URL 即可

# Vision - Qwen-VL 本地模型（可选）
VISION_QWEN_API_URL = "http://127.0.0.1:8000/v1/chat/completions"
                                      # vLLM/transformers HTTP API 地址
VISION_QWEN_MODEL_NAME = "Qwen/Qwen2-VL-7B-Instruct"

# Vision - OCR 轻量提取（免费兜底方案，仅提取画面文字）
# 基于 EasyOCR（PyTorch），pip install easyocr
VISION_OCR_LANG = "ch"                # OCR语言: ch / en / ch_en

# 提取超时
VIDEO_EXTRACTION_PER_POST_TIMEOUT = 120  # ★ 单个视频帖子最大处理时间(秒)
VIDEO_EXTRACTION_CONCURRENCY = 2         # ★ 并发处理的视频数(控制API/CPU资源占用)

# 文本合并配置
VIDEO_ENRICHED_CONTENT_MAX_CHARS = 2000  # ★ 合并后文本最大长度(截断后送给情感分析)

# ==================== 重试配置 ====================
MAX_RETRY_COUNT = 1                # [优化: 3→1, 获取不到不重试]
RETRY_DELAY_SECONDS = 3            # [优化: 10→3]
SEARCH_RETRY_COUNT = 1             # [优化: 3→1, 搜索失败不重试]
SEARCH_RETRY_DELAY = 2             # [优化: 5→2]
