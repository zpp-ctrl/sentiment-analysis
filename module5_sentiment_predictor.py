# -*- coding: utf-8 -*-
"""
============================================================
模块5: 情感分析涨跌预测器
============================================================
原理:
  1. 按四大指数关键词将帖子分配到对应指数
  2. 每篇帖子由大V的粉丝数加权
  3. 计算每个指数的加权看多/看空得分
  4. 综合判定次日涨跌方向（仅预测涨跌，不预测具体指数值）
  5. 生成预测摘要和综合分析报告
"""

import logging
import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from config import INDEX_CODE_MAP, POST_MAX_AGE_HOURS

logger = logging.getLogger(__name__)

# ★ 时间衰减半衰期(小时)：帖子每过这么长时间，权重减半
TIME_DECAY_HALF_LIFE_HOURS = 6.0

# ★ 四大指数相关的关键词映射
INDEX_KEYWORD_MAP = {
    "000001.SH": [
        "上证指数", "上证综指", "上证", "大盘指数", "沪指", "A股大盘",
        "上证50", "大盘走势", "主板", "沪市",
    ],
    "000300.SH": [
        "沪深300", "沪深", "300指数", "沪深三百", "IF", "大盘蓝筹",
        "蓝筹股", "白马股", "权重股",
    ],
    "000905.SH": [
        "中证500", "中证", "500指数", "中盘股", "IC", "中证五百",
        "中小盘", "成长股", "二线蓝筹",
    ],
    "000852.SH": [
        "中证1000", "中证一千", "1000指数", "小盘股", "中小创",
        "创业板", "科创板", "题材股", "小市值",
    ],
}

# ★ 通用看多/看空信号词（全局情绪）
BULLISH_PATTERNS = [
    "看涨", "看多", "牛市", "上涨", "反弹", "突破", "利好",
    "大涨", "暴涨", "走强", "起飞", "抄底", "买入", "加仓",
    "放量", "翻红", "冲高", "上行", "利好", "企稳", "V型反转",
]
BEARISH_PATTERNS = [
    "看跌", "看空", "熊市", "下跌", "破位", "跳水", "利空",
    "大跌", "暴跌", "走弱", "崩盘", "清仓", "卖出", "减仓",
    "缩量", "翻绿", "回调", "下行", "套牢", "割肉",
]


class SentimentPredictor:
    """
    基于情感分析的涨跌预测器
    替换原来的 LightGBM 模型
    """

    def __init__(self, reference_time: Optional[datetime] = None):
        self.follower_weight_base = 100000  # 10万粉为基准权重1.0
        self.reference_time = reference_time or datetime.now()  # 用于计算帖子时效性

    def classify_post_to_index(self, content: str) -> List[str]:
        """
        将帖子分配到一个或多个指数
        返回: [index_code, ...]
        修复: 不再使用 "general" 标记——不匹配的帖子以降低的权重参与所有指数
        """
        content = str(content)
        matched = []
        for code, keywords in INDEX_KEYWORD_MAP.items():
            for kw in keywords:
                if kw in content:
                    matched.append(code)
                    break
        # 没有匹配到具体指数 → 返回空列表，由调用方以降低权重处理
        return matched

    def compute_sentiment_score(self, content: str) -> float:
        """
        计算单条帖子的情感得分
        > 0.5: 偏多, < 0.5: 偏空, = 0.5: 中性
        """
        content = str(content)
        bullish_hits = sum(1 for w in BULLISH_PATTERNS if w in content)
        bearish_hits = sum(1 for w in BEARISH_PATTERNS if w in content)

        if bullish_hits == 0 and bearish_hits == 0:
            return 0.5  # 中性
        return (bullish_hits + 1) / (bullish_hits + bearish_hits + 2)

    def _compute_time_decay(self, post_time) -> float:
        """
        计算帖子的时间衰减因子
        - 刚发布的帖子权重 ≈ 1.0
        - 半衰期后权重 = 0.5
        - 超过POST_MAX_AGE_HOURS的帖子权重趋近于0

        使用指数衰减: decay = 2^(-age_hours / half_life_hours)
        """
        if post_time is None or pd.isna(post_time):
            return 0.5  # 未知时间给中等权重

        try:
            post_dt = pd.to_datetime(post_time)
            age_hours = (self.reference_time - post_dt).total_seconds() / 3600.0
            if age_hours < 0:
                age_hours = 0  # 未来时间（时钟偏差）按刚发布处理
            # 指数衰减
            decay = math.pow(2.0, -age_hours / TIME_DECAY_HALF_LIFE_HOURS)
            # 超过最大窗口的帖子给最低权重
            if age_hours > POST_MAX_AGE_HOURS:
                decay = min(decay, 0.05)
            return max(decay, 0.01)  # 不低于0.01，不完全丢弃
        except Exception:
            return 0.5

    def predict_for_index(
        self,
        index_code: str,
        posts_df: pd.DataFrame,
    ) -> Dict:
        """
        对单个指数进行预测

        Args:
            index_code: 指数代码
            posts_df: 帖子DataFrame (需包含 post_content, follower_count, sentiment)

        Returns:
            {index_code, direction, confidence, bullish_score, bearish_score, ...}
        """
        if posts_df is None or len(posts_df) == 0:
            return {
                "index_code": index_code,
                "index_name": INDEX_CODE_MAP.get(index_code, index_code),
                "predict_direction": "neutral",
                "confidence": 0.0,
                "bullish_score": 0.5,
                "bearish_score": 0.5,
                "weighted_posts": 0,
            }

        # 筛选与该指数相关的帖子
        index_posts = []
        for _, row in posts_df.iterrows():
            content = str(row.get("post_content", ""))
            matched_indices = self.classify_post_to_index(content)
            if index_code in matched_indices:
                # 精确匹配：完整权重
                index_posts.append((row, 1.0))
            elif not matched_indices:
                # 未匹配任何指数：以降低的权重(0.3)参与，避免"general"污染
                index_posts.append((row, 0.3))

        if not index_posts:
            # 没有任何相关帖子，返回中性
            return {
                "index_code": index_code,
                "index_name": INDEX_CODE_MAP.get(index_code, index_code),
                "predict_direction": "neutral",
                "confidence": 0.0,
                "bullish_score": 0.5,
                "bearish_score": 0.5,
                "weighted_posts": 0,
            }

        # 粉丝加权 + 时间衰减计算
        total_weight = 0.0
        weighted_bullish = 0.0
        weighted_bearish = 0.0

        for row, index_multiplier in index_posts:
            followers = float(row.get("follower_count", 100000))
            weight = max(0.1, np.log10(max(followers, 1000)) - 2)  # log加权

            # ★ 指数匹配乘数: 精确匹配=1.0, 通用帖=0.3
            weight *= index_multiplier

            # ★ 时间衰减：旧帖子权重降低
            post_time = row.get("post_time", None)
            time_decay = self._compute_time_decay(post_time)
            weight *= time_decay

            sentiment = row.get("sentiment", "neutral")

            # ★ 优先使用 LLM 的 sentiment_score，fallback 到关键词计数
            llm_score = row.get("sentiment_score", None)
            if llm_score is not None and not pd.isna(llm_score):
                score = float(llm_score)
            else:
                score = self.compute_sentiment_score(str(row.get("post_content", "")))

            # ★ LLM 置信度加权：高置信度帖子权重更大，低置信度权重打折扣
            llm_conf = row.get("llm_confidence", None)
            if llm_conf is not None and not pd.isna(llm_conf):
                weight *= max(0.3, min(2.0, float(llm_conf)))

            # ★ 修复: 按score比例分配权重，中性帖子不再只增加分母
            if sentiment == "bullish" or score > 0.55:
                weighted_bullish += weight
            elif sentiment == "bearish" or score < 0.45:
                weighted_bearish += weight
            else:
                # 中性帖子: 按score比例分配(score=0.5→各半, score=0.53→偏多53%)
                weighted_bullish += weight * score
                weighted_bearish += weight * (1.0 - score)

            total_weight += weight

        if total_weight == 0:
            return {
                "index_code": index_code,
                "index_name": INDEX_CODE_MAP.get(index_code, index_code),
                "predict_direction": "neutral",
                "confidence": 0.0,
                "bullish_score": 0.5,
                "bearish_score": 0.5,
                "weighted_posts": len(index_posts),
            }

        bullish_score = weighted_bullish / total_weight
        bearish_score = weighted_bearish / total_weight
        net_score = bullish_score - bearish_score

        # 判定方向（新增 偏多/偏空 档，避免明显偏多被压成中性）
        if net_score > 0.15:
            direction = "up"
        elif net_score > 0.05:
            direction = "slightly_up"
        elif net_score < -0.15:
            direction = "down"
        elif net_score < -0.05:
            direction = "slightly_down"
        else:
            # |net_score| <= 0.05，多空基本均衡
            direction = "neutral"

        # ★ 置信度 = 多空净差的单调映射（50%~95%），净差越大置信度越高
        #   修复: 原先 "up/down" 档用 net*1.5 少了 0.5 底，导致越强反而越低
        confidence = min(0.5 + abs(net_score), 0.95)

        return {
            "index_code": index_code,
            "index_name": INDEX_CODE_MAP.get(index_code, index_code),
            "predict_direction": direction,
            "predict_prob": round(0.5 + net_score / 2, 4),
            "confidence": round(confidence, 4),
            "bullish_score": round(bullish_score, 4),
            "bearish_score": round(bearish_score, 4),
            "net_sentiment": round(net_score, 4),
            "weighted_posts": len(index_posts),
        }

    def predict_all_indices(self, posts_df: pd.DataFrame) -> List[Dict]:
        """
        预测四大指数次日涨跌

        Returns:
            [{index_code, direction, confidence, ...}, ...]
        """
        results = []
        for code in INDEX_CODE_MAP:
            result = self.predict_for_index(code, posts_df)
            results.append(result)
            logger.info(
                "%s: %s (置信度=%.2f%%, 多=%.3f 空=%.3f, 帖=%d)",
                result["index_name"], result["predict_direction"],
                result["confidence"] * 100,
                result["bullish_score"], result["bearish_score"],
                result["weighted_posts"],
            )
        return results

    def generate_summary(self, predictions: List[Dict]) -> str:
        """生成预测摘要文本"""
        lines = ["=== 四大指数次日涨跌预测 ===\n"]
        for p in predictions:
            direction_zh = {"up": "📈 看涨", "slightly_up": "📈 偏多", "slightly_down": "📉 偏空", "down": "📉 看跌", "neutral": "➡️ 中性"}
            d = direction_zh.get(p["predict_direction"], p["predict_direction"])
            lines.append(
                f"{p['index_name']:6s}: {d} | "
                f"置信度={p['confidence']:.1%} | "
                f"多={p['bullish_score']:.3f} 空={p['bearish_score']:.3f} | "
                f"相关帖={p['weighted_posts']}"
            )
        return "\n".join(lines)


# ==================== 便捷函数 ====================

def run_sentiment_prediction(posts_df: pd.DataFrame,
                            reference_time: Optional[datetime] = None) -> List[Dict]:
    """便捷函数: 输入帖子 → 输出四大指数涨跌预测"""
    predictor = SentimentPredictor(reference_time=reference_time)
    return predictor.predict_all_indices(posts_df)
