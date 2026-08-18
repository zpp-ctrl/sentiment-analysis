# -*- coding: utf-8 -*-
"""
============================================================
模块6: 次日收盘回测 & 准确率统计
============================================================
功能:
  1. T+1日15:35自动拉取4个指数真实涨跌幅
  2. 匹配T日生成的预测记录（基于情感分析的涨跌预测）
  3. 自动统计: 分指数准确率、整体综合准确率
  4. 回填 actual_direction/actual_pct_change/is_correct 字段
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from config import INDEX_CODE_MAP, MAX_RETRY_COUNT, RETRY_DELAY_SECONDS
from module4_index_data import IndexDataProvider, TradingCalendar

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    回测比对引擎
    负责: 获取实际涨跌 → 匹配预测 → 统计准确率
    """

    def __init__(self):
        self.provider = IndexDataProvider()

    def run_backtest(
        self,
        predictions_df: pd.DataFrame,
        target_date: date,
    ) -> pd.DataFrame:
        """
        执行回测比对

        Args:
            predictions_df: T日预测记录，需包含:
              predict_date, target_date, index_code, index_name,
              predict_direction, predict_prob
            target_date: T+1日（回测目标日期）

        Returns:
            包含回测结果的DataFrame，新增字段:
              actual_direction, actual_pct_change, is_correct, backtest_time
        """
        if predictions_df is None or len(predictions_df) == 0:
            logger.warning("无预测记录，跳过回测")
            return predictions_df

        logger.info(
            "开始执行 %s 回测比对，共 %d 条预测记录",
            target_date, len(predictions_df),
        )

        result_df = predictions_df.copy()
        target_date_str = target_date.strftime("%Y%m%d")

        actual_directions = []
        actual_pct_changes = []
        is_correct_list = []

        for _, row in result_df.iterrows():
            index_code = row["index_code"]
            index_name = row["index_name"]
            pred_dir = row["predict_direction"]

            # 获取实际涨跌幅
            actual_pct = self.provider.get_actual_pct_change(
                index_code, target_date_str
            )

            if actual_pct is None:
                logger.warning("%s (%s) 无法获取 %s 实际涨跌幅",
                               index_name, index_code, target_date)
                actual_directions.append(None)
                actual_pct_changes.append(None)
                is_correct_list.append(None)
                continue

            actual_dir = "up" if actual_pct > 0 else "down"
            # 偏多/偏空 归入 看涨/看跌 计算准确率
            pred_norm = ("up" if pred_dir in ("up", "slightly_up")
                         else "down" if pred_dir in ("down", "slightly_down")
                         else pred_dir)
            is_correct = 1 if pred_norm == actual_dir else 0

            actual_directions.append(actual_dir)
            actual_pct_changes.append(round(actual_pct, 4))
            is_correct_list.append(is_correct)

            logger.info(
                "  %s: 预测=%s | 实际=%+.4f%%(%s) | %s",
                index_name, pred_dir, actual_pct, actual_dir,
                "✓正确" if is_correct else "✗错误",
            )

        result_df["actual_direction"] = actual_directions
        result_df["actual_pct_change"] = actual_pct_changes
        result_df["is_correct"] = is_correct_list
        result_df["backtest_time"] = datetime.now()

        return result_df

    def calculate_accuracy(
        self,
        backtest_df: pd.DataFrame,
    ) -> Dict:
        """
        计算回测准确率指标

        Returns:
            {
                "overall_accuracy": float,          # 整体综合准确率
                "total_predictions": int,            # 总预测数
                "correct_count": int,                # 正确数
                "per_index_accuracy": {              # 分指数准确率
                    "000001.SH": {"name": "上证指数", "accuracy": 0.65, "correct": 13, "total": 20},
                    ...
                },
                "backtest_date": date,
            }
        """
        if backtest_df is None or len(backtest_df) == 0:
            return {
                "overall_accuracy": 0.0,
                "total_predictions": 0,
                "correct_count": 0,
                "per_index_accuracy": {},
                "backtest_date": date.today(),
            }

        df = backtest_df.dropna(subset=["is_correct"])

        total = len(df)
        correct = int(df["is_correct"].sum())
        overall_acc = round(correct / total, 4) if total > 0 else 0.0

        per_index = {}
        for code, name in INDEX_CODE_MAP.items():
            subset = df[df["index_code"] == code]
            if len(subset) > 0:
                sub_total = len(subset)
                sub_correct = int(subset["is_correct"].sum())
                sub_acc = round(sub_correct / sub_total, 4) if sub_total > 0 else 0.0
                per_index[code] = {
                    "name": name,
                    "accuracy": sub_acc,
                    "correct": sub_correct,
                    "total": sub_total,
                }

        result = {
            "overall_accuracy": overall_acc,
            "total_predictions": total,
            "correct_count": correct,
            "per_index_accuracy": per_index,
            "backtest_date": date.today(),
        }

        # 日志输出
        logger.info("=" * 60)
        logger.info("  回测准确率统计报告")
        logger.info("=" * 60)
        logger.info("  回测日期: %s", result["backtest_date"])
        logger.info("  总预测数: %d", total)
        logger.info("  正确数量: %d", correct)
        logger.info("  整体准确率: %.2f%%", overall_acc * 100)
        logger.info("  --- 分指数准确率 ---")
        for code, info in per_index.items():
            logger.info(
                "    %s (%s): %.2f%% (%d/%d)",
                info["name"], code,
                info["accuracy"] * 100,
                info["correct"], info["total"],
            )
        logger.info("=" * 60)

        return result

    def run_full_backtest(
        self,
        predictions_df: pd.DataFrame,
        target_date: Optional[date] = None,
    ) -> Tuple[pd.DataFrame, Dict]:
        """
        完整回测流程: 比对 + 统计
        """
        if target_date is None:
            target_date = TradingCalendar.get_latest_trading_day()

        backtest_df = self.run_backtest(predictions_df, target_date)
        accuracy = self.calculate_accuracy(backtest_df)

        return backtest_df, accuracy


# ==================== 便捷函数 ====================

def run_backtest_and_report(predictions_df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """
    便捷函数: 输入预测记录 → 回测比对 → 输出结果+统计
    """
    engine = BacktestEngine()
    return engine.run_full_backtest(predictions_df)


# ==================== 本地测试入口 ====================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    # 模拟T日预测记录
    today = date.today()
    mock_predictions = pd.DataFrame([
        {
            "predict_date": today,
            "target_date": today + timedelta(days=1),
            "index_code": code,
            "index_name": name,
            "predict_direction": np.random.choice(["up", "down"]),
            "predict_prob": round(np.random.uniform(0.4, 0.6), 4),
        }
        for code, name in INDEX_CODE_MAP.items()
    ])

    print("\n模拟预测记录:")
    print(mock_predictions.to_string(index=False))

    engine = BacktestEngine()
    backtest_df, accuracy = engine.run_full_backtest(mock_predictions)

    print("\n回测结果:")
    if backtest_df is not None:
        cols = ["index_name", "predict_direction", "actual_pct_change",
                "actual_direction", "is_correct"]
        print(backtest_df[cols].to_string(index=False))
    print(f"\n整体准确率: {accuracy['overall_accuracy']:.2%}")