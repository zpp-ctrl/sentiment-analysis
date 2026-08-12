# -*- coding: utf-8 -*-
"""
============================================================
模块3: 情绪指标汇总计算
============================================================
功能:
  1. 按日期 + 账号等级维度统计看多/看空/中性占比
  2. 区分 all / top1000 / 10w_plus 三个维度
  3. 输出结构化统计结果
"""

import logging
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class SentimentAggregator:
    """
    情绪指标汇总器
    输入: 带情感分类标签的帖子DataFrame
    输出: 按维度汇总的情绪统计数据
    """

    def __init__(self):
        self.stats_records: List[Dict] = []

    def aggregate(
        self,
        df_posts: pd.DataFrame,
        stat_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """
        汇总情绪指标

        Args:
            df_posts: 包含 sentiment, sentiment_score, account_level 的帖子表
            stat_date: 统计日期，默认当天

        Returns:
            DataFrame 包含以下字段:
              stat_date, account_level, total_posts,
              bullish_count, bearish_count, neutral_count,
              bullish_ratio, bearish_ratio, neutral_ratio,
              avg_sentiment_score
        """
        if df_posts is None or len(df_posts) == 0:
            logger.warning("帖子数据为空，生成空统计记录")
            return self._empty_stats(stat_date)

        if stat_date is None:
            stat_date = date.today()

        logger.info("开始汇总 %s 的情绪指标...", stat_date)

        stats_list = []

        # 维度1: 全部汇总 (all)
        stats_all = self._calc_stats(
            df_posts, stat_date, account_level="all"
        )
        stats_list.append(stats_all)

        # 维度2: 头部TOP1000
        df_top = df_posts[df_posts["account_level"] == "top1000"]
        if len(df_top) > 0:
            stats_top = self._calc_stats(
                df_top, stat_date, account_level="top1000"
            )
            stats_list.append(stats_top)
        else:
            stats_list.append(self._empty_stats_row(stat_date, "top1000"))

        # 维度3: 10w+普通博主
        df_10w = df_posts[df_posts["account_level"] == "10w_plus"]
        if len(df_10w) > 0:
            stats_10w = self._calc_stats(
                df_10w, stat_date, account_level="10w_plus"
            )
            stats_list.append(stats_10w)
        else:
            stats_list.append(self._empty_stats_row(stat_date, "10w_plus"))

        result_df = pd.DataFrame(stats_list)
        self.stats_records = stats_list

        # 日志输出核心指标
        for _, row in result_df.iterrows():
            logger.info(
                "[%s | %s] 总数=%d | 看多=%.2f%% | 看空=%.2f%% | 中性=%.2f%% | 均分=%.4f",
                row["stat_date"], row["account_level"],
                row["total_posts"],
                row["bullish_ratio"] * 100,
                row["bearish_ratio"] * 100,
                row["neutral_ratio"] * 100,
                row["avg_sentiment_score"],
            )

        return result_df

    def _calc_stats(
        self,
        df: pd.DataFrame,
        stat_date: date,
        account_level: str,
    ) -> Dict:
        """计算单个维度的情绪统计"""
        total = len(df)
        bullish = int((df["sentiment"] == "bullish").sum())
        bearish = int((df["sentiment"] == "bearish").sum())
        neutral = int((df["sentiment"] == "neutral").sum())

        return {
            "stat_date": stat_date,
            "account_level": account_level,
            "total_posts": total,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "bullish_ratio": round(bullish / total, 4) if total > 0 else 0.0,
            "bearish_ratio": round(bearish / total, 4) if total > 0 else 0.0,
            "neutral_ratio": round(neutral / total, 4) if total > 0 else 0.0,
            "avg_sentiment_score": round(float(df["sentiment_score"].mean()), 4)
            if total > 0 else 0.5000,
        }

    def _empty_stats(self, stat_date: Optional[date]) -> pd.DataFrame:
        """返回空统计结果"""
        if stat_date is None:
            stat_date = date.today()
        rows = []
        for level in ["all", "top1000", "10w_plus"]:
            rows.append(self._empty_stats_row(stat_date, level))
        return pd.DataFrame(rows)

    @staticmethod
    def _empty_stats_row(stat_date: date, account_level: str) -> Dict:
        """单行空统计"""
        return {
            "stat_date": stat_date,
            "account_level": account_level,
            "total_posts": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "bullish_ratio": 0.0,
            "bearish_ratio": 0.0,
            "neutral_ratio": 0.0,
            "avg_sentiment_score": 0.5000,
        }

    def get_sentiment_summary(self) -> Dict:
        """
        获取情绪摘要（供ML模块使用）
        返回全体汇总的关键指标 dict
        """
        if not self.stats_records:
            return {}

        for rec in self.stats_records:
            if rec["account_level"] == "all":
                return rec
        return {}


# ==================== 便捷函数 ====================

def run_sentiment_aggregation(
    df_posts: pd.DataFrame,
    stat_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    便捷函数: 输入帖子表 → 输出情绪统计表
    """
    aggregator = SentimentAggregator()
    stats_df = aggregator.aggregate(df_posts, stat_date)
    return stats_df


# ==================== 本地测试入口 ====================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    # 模拟已标注情感的帖子数据
    test_data = {
        "post_id": [f"test_{i}" for i in range(100)],
        "platform": ["douyin"] * 50 + ["xiaohongshu"] * 50,
        "account_level": ["top1000"] * 40 + ["10w_plus"] * 60,
        "sentiment": ["bullish"] * 40 + ["bearish"] * 30 + ["neutral"] * 30,
        "sentiment_score": np.random.uniform(0.3, 0.7, 100).tolist(),
    }
    df_test = pd.DataFrame(test_data)
    result = run_sentiment_aggregation(df_test)
    print("\n情绪统计结果:")
    print(result.to_string(index=False))