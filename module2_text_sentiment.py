# -*- coding: utf-8 -*-
"""
============================================================
模块2: 文本清洗 & 财经专用多空情感分类
============================================================
功能:
  1. 文本预处理：去噪、去特殊字符、统一格式
  2. 构建财经领域多空情感词典
  3. 对帖子文本做多空情感分类，返回 bullish/bearish/neutral
  4. 输出情感得分（0~1，越高越偏多）
"""

import re
import logging
from typing import Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# ==================== 财经多空情感词典 ====================

# 看多关键词（权重较高）
BULLISH_KEYWORDS = {
    # 强看多信号（权重2.0）
    "暴涨": 2.0, "涨停": 2.0, "突破": 1.8, "放量大涨": 2.0,
    "强势上攻": 2.0, "大阳线": 1.8, "主升浪": 2.0, "趋势向上": 1.8,
    "牛市": 2.0, "起飞": 1.8, "爆发": 1.8, "井喷": 2.0,
    "全线飘红": 2.0, "涨停潮": 2.0, "技术性牛市": 2.0,
    # 一般看多信号（权重1.2~1.5）
    "上涨": 1.2, "拉升": 1.3, "看好": 1.3, "看多": 1.5,
    "反弹": 1.2, "回暖": 1.2, "利好": 1.3, "加仓": 1.4,
    "买入": 1.5, "增持": 1.3, "净流入": 1.3, "放量": 1.2,
    "走强": 1.3, "企稳": 1.2, "修复": 1.2, "超跌反弹": 1.3,
    "估值修复": 1.3, "估值优势": 1.2, "底部": 1.2,
    "金叉": 1.5, "均线多头": 1.5, "量价齐升": 1.5,
    "政策底": 1.4, "市场底": 1.4, "双底": 1.3,
    "抄底": 1.5, "布局": 1.2, "配置价值": 1.3,
    "资金流入": 1.3, "北向资金流入": 1.4, "南向资金流入": 1.3,
    "主力净买入": 1.5, "大单流入": 1.4, "机构增仓": 1.4,
    "业绩超预期": 1.3, "景气度提升": 1.3, "盈利改善": 1.2,
    "成交量放大": 1.2, "温和放量": 1.1, "持续放量": 1.3,
    "向上突破": 1.5, "创新高": 1.5, "新高": 1.3,
    "领涨": 1.4, "涨幅居前": 1.3, "强势": 1.2,
    "红盘": 1.1, "收涨": 1.1, "普涨": 1.3,
    "复苏": 1.2, "回升": 1.2, "转好": 1.1,
    "降准": 1.3, "降息": 1.3, "流动性宽松": 1.4,
    "宽松": 1.2, "稳增长": 1.2, "刺激政策": 1.2,
    "外资加仓": 1.4, "外资流入": 1.4, "外资看好": 1.4,
}

# 看空关键词（权重较高）
BEARISH_KEYWORDS = {
    # 强看空信号（权重2.0）
    "暴跌": 2.0, "跌停": 2.0, "崩盘": 2.0, "股灾": 2.0,
    "恐慌": 1.8, "踩踏": 2.0, "熔断": 2.0, "爆仓": 2.0,
    "断崖式下跌": 2.0, "破位下行": 1.8,
    "熊市": 2.0, "大阴线": 1.8, "主跌浪": 2.0, "趋势向下": 1.8,
    # 一般看空信号（权重1.2~1.5）
    "下跌": 1.2, "跳水": 1.4, "走弱": 1.3, "看空": 1.5,
    "回调": 1.1, "调整": 1.1, "利空": 1.3, "减仓": 1.4,
    "卖出": 1.5, "减持": 1.3, "净流出": 1.3, "缩量": 1.2,
    "破位": 1.4, "承压": 1.3, "风险": 1.2, "下行": 1.3,
    "杀跌": 1.5, "补跌": 1.3, "冲高回落": 1.3,
    "死叉": 1.5, "均线空头": 1.5, "量价背离": 1.4,
    "资金出逃": 1.5, "资金流出": 1.3, "北向资金流出": 1.4,
    "主力净卖出": 1.5, "大单流出": 1.4, "机构减仓": 1.4,
    "业绩不及预期": 1.3, "业绩暴雷": 1.6, "亏损": 1.3,
    "暴雷": 1.5, "退市": 1.8, "ST": 1.5,
    "缩量下跌": 1.2, "阴跌": 1.3, "持续走低": 1.4,
    "跌破": 1.4, "创新低": 1.5, "新低": 1.3,
    "领跌": 1.4, "跌幅居前": 1.3, "弱势": 1.2,
    "绿盘": 1.1, "收跌": 1.1, "普跌": 1.3,
    "衰退": 1.4, "下滑": 1.2, "恶化": 1.3,
    "加息": 1.3, "收紧": 1.3, "流动性收紧": 1.4,
    "缩表": 1.3, "通胀": 1.1, "滞胀": 1.4,
    "地缘风险": 1.3, "黑天鹅": 1.5, "灰犀牛": 1.4,
    "外资出逃": 1.4, "外资减持": 1.4, "外资流出": 1.4,
}

# 否定前缀模式（反转情感）
NEGATION_PATTERNS = [
    r"不(?:会|可能|太|是|一定|能|会再|再)",
    r"没(?:有|什么|啥)",
    r"未(?:能|必|见|出现)",
    r"难(?:以|道|说|言)",
    r"无(?:法|力|需|需担心)",
    r"缺乏",
    r"并非",
    r"谈不上",
]

# 转折词（后面内容更重要）
TRANSITION_WORDS = [
    "但是", "但", "然而", "不过", "可是",
    "却", "尽管如此", "虽然如此", "反之",
]


# ==================== 财经相关性过滤 ====================

def is_finance_post(text: str) -> bool:
    """
    判断帖子是否为财经相关内容。
    用于过滤财经博主发布的非财经内容（做饭/养花/演唱会/生活vlog等），
    避免这些无关帖子被当作"中性"拉高中性占比、稀释真实市场情绪。

    返回: True=财经相关，False=无关
    """
    from config import FINANCE_POST_KEYWORDS

    text = str(text or "")
    if not text.strip():
        return False
    return any(kw in text for kw in FINANCE_POST_KEYWORDS)


def filter_finance_posts(
    df: pd.DataFrame,
    content_col: str = "post_content",
) -> pd.DataFrame:
    """
    从帖子表中过滤出财经相关帖子。

    Args:
        df: 帖子 DataFrame
        content_col: 文本列名

    Returns:
        仅含财经相关帖子的 DataFrame（新增 is_finance 列）
    """
    if df is None or len(df) == 0:
        return df

    df = df.copy()
    df["is_finance"] = df[content_col].fillna("").map(is_finance_post)
    n_total = len(df)
    n_finance = int(df["is_finance"].sum())
    n_other = n_total - n_finance
    logger.info(
        "财经相关性过滤: 总%d条 → 保留%d条财经帖, 过滤%d条无关帖(%.1f%%)",
        n_total, n_finance, n_other, 100.0 * n_other / n_total if n_total else 0,
    )
    return df[df["is_finance"]].drop(columns=["is_finance"]).reset_index(drop=True)


# ==================== 文本清洗函数 ====================

def clean_text(text: str) -> str:
    """
    文本清洗预处理：
    1. 去除URL链接
    2. 去除HTML标签
    3. 去除特殊Unicode字符
    4. 统一全角→半角
    5. 去除多余空白
    6. 去除纯emoji/符号行
    """
    if not isinstance(text, str) or not text.strip():
        return ""

    # 去除URL
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)

    # 去除HTML标签
    text = re.sub(r'<[^>]+>', '', text)

    # 去除微博/抖音话题标签格式 #xxx#
    text = re.sub(r'#[一-鿿\w]+#', '', text)

    # 去除@提及
    text = re.sub(r'@\S+', '', text)

    # 保留中英文、数字、标点符号
    text = re.sub(r'[^一-鿿\w\s.,;:!?！？。，、；：""''（）()\-+%①②③④⑤⑥⑦⑧⑨⑩]', ' ', text)

    # 统一全角数字/字母 → 半角
    fullwidth_to_half = {
        '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
        '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
        'Ａ': 'A', 'Ｂ': 'B', 'Ｃ': 'C', 'Ｄ': 'D', 'Ｅ': 'E',
        'Ｆ': 'F', 'Ｇ': 'G', 'Ｈ': 'H', 'Ｉ': 'I', 'Ｊ': 'J',
        'Ｋ': 'K', 'Ｌ': 'L', 'Ｍ': 'M', 'Ｎ': 'N', 'Ｏ': 'O',
        'Ｐ': 'P', 'Ｑ': 'Q', 'Ｒ': 'R', 'Ｓ': 'S', 'Ｔ': 'T',
        'Ｕ': 'U', 'Ｖ': 'V', 'Ｗ': 'W', 'Ｘ': 'X', 'Ｙ': 'Y',
        'Ｚ': 'Z', 'ａ': 'a', 'ｂ': 'b', 'ｃ': 'c', 'ｄ': 'd',
        'ｅ': 'e', 'ｆ': 'f', 'ｇ': 'g', 'ｈ': 'h', 'ｉ': 'i',
        'ｊ': 'j', 'ｋ': 'k', 'ｌ': 'l', 'ｍ': 'm', 'ｎ': 'n',
        'ｏ': 'o', 'ｐ': 'p', 'ｑ': 'q', 'ｒ': 'r', 'ｓ': 's',
        'ｔ': 't', 'ｕ': 'u', 'ｖ': 'v', 'ｗ': 'w', 'ｘ': 'x',
        'ｙ': 'y', 'ｚ': 'z', '　': ' ', '！': '!', '？': '?',
        '。': '.', '，': ',', '；': ';', '：': ':',
    }
    for full, half in fullwidth_to_half.items():
        text = text.replace(full, half)

    # 去除多余空白
    text = re.sub(r'\s+', ' ', text).strip()

    return text


# ==================== 情感分类核心函数 ====================

def _extract_sentences(text: str) -> list:
    """按标点分句"""
    sentences = re.split(r'[。！？!?\n；;]+', text)
    return [s.strip() for s in sentences if s.strip()]


def _has_negation_before(text: str, keyword: str, window: int = 6) -> bool:
    """
    检测关键词前N个字符内是否存在否定词
    如果存在否定词，情感倾向反转
    """
    idx = text.find(keyword)
    if idx < 0:
        return False
    prefix = text[max(0, idx - window):idx]
    for pattern in NEGATION_PATTERNS:
        if re.search(pattern, prefix):
            return True
    return False


def _has_transition_after(text: str, keyword: str, window: int = 15) -> bool:
    """
    检测关键词后是否紧跟转折词
    如果有，该句权重可能被削弱
    """
    idx = text.find(keyword)
    if idx < 0:
        return False
    suffix = text[idx + len(keyword):idx + len(keyword) + window]
    for tw in TRANSITION_WORDS:
        if tw in suffix:
            return True
    return False


def classify_sentiment(text: str) -> Tuple[str, float]:
    """
    财经多空情感分类核心函数

    算法:
      1. 清洗文本
      2. 逐句扫描，统计看多/看空关键词命中次数及权重
      3. 考虑否定前缀（反转情感）+ 转折词（削弱当前句权重）
      4. 归一化得分到 [0, 1] 区间
         - 得分 > 0.55 → bullish（看多）
         - 得分 < 0.45 → bearish（看空）
         - 0.45 ≤ 得分 ≤ 0.55 → neutral（中性）

    Args:
        text: 原始帖子文本

    Returns:
        (sentiment_label, sentiment_score)
        sentiment_label ∈ {"bullish", "bearish", "neutral"}
        sentiment_score ∈ [0.0, 1.0]
    """
    # 1. 清洗
    cleaned = clean_text(text)
    if not cleaned:
        return ("neutral", 0.50)
    if len(cleaned) < 10:
        return ("neutral", 0.50)

    # 2. 分句
    sentences = _extract_sentences(cleaned)
    if not sentences:
        return ("neutral", 0.50)

    # 3. 逐句统计
    total_bullish_weight = 0.0
    total_bearish_weight = 0.0

    for sent in sentences:
        # 看多关键词
        for kw, weight in BULLISH_KEYWORDS.items():
            if kw in sent:
                if _has_negation_before(sent, kw):
                    # 否定前缀 → 反转为看空
                    total_bearish_weight += weight * 0.8
                elif _has_transition_after(sent, kw):
                    # 有转折词 → 削弱权重
                    total_bullish_weight += weight * 0.5
                else:
                    total_bullish_weight += weight

        # 看空关键词
        for kw, weight in BEARISH_KEYWORDS.items():
            if kw in sent:
                if _has_negation_before(sent, kw):
                    # 否定前缀 → 反转为看多
                    total_bullish_weight += weight * 0.8
                elif _has_transition_after(sent, kw):
                    # 有转折词 → 削弱权重
                    total_bearish_weight += weight * 0.5
                else:
                    total_bearish_weight += weight

    # 4. 归一化得分
    total_weight = total_bullish_weight + total_bearish_weight
    if total_weight == 0:
        return ("neutral", 0.50)

    # sigmoid-like归一化: 得分在0~1之间
    sentiment_score = total_bullish_weight / total_weight

    # 边界平滑
    if sentiment_score > 0.60:
        return ("bullish", min(sentiment_score, 1.0))
    elif sentiment_score < 0.40:
        return ("bearish", max(sentiment_score, 0.0))
    else:
        return ("neutral", sentiment_score)


def classify_batch(df: pd.DataFrame, content_col: str = "post_content") -> pd.DataFrame:
    """
    批量情感分类
    对DataFrame中的帖子内容列逐行进行分类

    Args:
        df: 包含帖子内容的DataFrame
        content_col: 文本列名，默认 "post_content"

    Returns:
        添加了 sentiment 和 sentiment_score 列的DataFrame
    """
    if df is None or len(df) == 0:
        logger.warning("输入DataFrame为空，跳过情感分类")
        return df

    logger.info("开始批量情感分类，共 %d 条帖子...", len(df))

    sentiments = []
    scores = []

    for idx, row in df.iterrows():
        try:
            text = row.get(content_col, "")
            label, score = classify_sentiment(str(text))
            sentiments.append(label)
            scores.append(round(score, 4))
        except Exception as exc:
            logger.error("情感分类异常 [row=%d]: %s", idx, str(exc))
            sentiments.append("neutral")
            scores.append(0.5000)

    df = df.copy()
    df["sentiment"] = sentiments
    df["sentiment_score"] = scores

    # 统计分布
    dist = df["sentiment"].value_counts().to_dict()
    logger.info(
        "情感分类完成: bullish=%d, bearish=%d, neutral=%d",
        dist.get("bullish", 0), dist.get("bearish", 0), dist.get("neutral", 0),
    )

    return df


# ==================== LLM 入口（可选） ====================

def classify_batch_llm(
    df: pd.DataFrame,
    content_col: str = "post_content",
) -> pd.DataFrame:
    """
    使用 Claude API 进行批量情感分类（LLM 方式）
    需要先设置 ANTHROPIC_API_KEY 环境变量 + config.py 中 LLM_ENABLED=True

    Args:
        df: 包含帖子内容的 DataFrame
        content_col: 文本列名

    Returns:
        添加了 sentiment, sentiment_score, llm_confidence, llm_reasoning 列的 DataFrame
    """
    from llm_sentiment import classify_with_llm
    logger.info("使用 LLM 模式进行情感分类")
    return classify_with_llm(df, content_col)


# ==================== 本地测试入口 ====================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    # 测试样例
    test_texts = [
        "今天A股暴涨，上证指数放量突破3300点，牛市来了！",
        "市场暴跌，恐慌情绪蔓延，建议减仓避险。",
        "北向资金大幅流入，外资持续看好A股，底部信号明确。",
        "缩量下跌趋势未改，但政策利好可能带来反弹。",
        "今天行情不温不火，多空分歧较大，维持震荡格局。",
        "加微信领取每日牛股，跟上老师操作稳赚不赔！",
    ]
    for t in test_texts:
        label, score = classify_sentiment(t)
        print(f"[{label}] score={score:.4f} | {t}")