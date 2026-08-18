# -*- coding: utf-8 -*-
"""
============================================================
补跑脚本: 余额恢复后重新做情感分类 + 次日预测 + 入库 + 推送
============================================================
用途: 今日主流水线因 DeepSeek 余额不足导致约 40% 帖子被降级为中性，
     重新对全部帖子做 LLM 分类，覆盖错误的预测结果。
"""
import os
import sys
import logging
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(module)s:%(lineno)d | %(message)s",
)
logger = logging.getLogger("reprocess_today")

import pandas as pd

from config import OUTPUT_DIR
from llm_sentiment import classify_with_llm
from module3_sentiment_aggregator import run_sentiment_aggregation
from module5_sentiment_predictor import SentimentPredictor
from module4_index_data import TradingCalendar
from module7_data_storage import MySQLManager, generate_excel_report
from module_notify import send_daily_prediction

RAW_CSV = os.path.join(OUTPUT_DIR, "20260818_douyin_posts.csv")
SENT_CSV = os.path.join(OUTPUT_DIR, "20260818_posts_with_sentiment.csv")


def main():
    today = date.today()
    logger.info("补跑日期: %s", today)

    # 0. 加载原始帖子
    df = pd.read_csv(RAW_CSV, encoding="utf-8-sig")
    logger.info("加载原始帖子: %d 条 (来自 %s)", len(df), RAW_CSV)

    # 1. 重新 LLM 情感分类（全量）
    logger.info("=" * 60)
    logger.info("步骤1: 重新 LLM 情感分类")
    logger.info("=" * 60)
    df = classify_with_llm(df, "post_content")
    df.to_csv(SENT_CSV, index=False, encoding="utf-8-sig")
    logger.info("分类结果已保存: %s", SENT_CSV)
    dist = df["sentiment"].value_counts().to_dict()
    n_neutral_fallback = int((df["llm_confidence"] == 0.0).sum())
    logger.info("情感分布: bullish=%s, bearish=%s, neutral=%s | 仍降级(conf=0)=%d",
                dist.get("bullish", 0), dist.get("bearish", 0),
                dist.get("neutral", 0), n_neutral_fallback)

    # 2. 情绪统计
    logger.info("=" * 60)
    logger.info("步骤2: 情绪指标汇总")
    logger.info("=" * 60)
    stats_df = run_sentiment_aggregation(df, stat_date=today)

    # 3. 预测（目标 = 下一交易日）
    logger.info("=" * 60)
    logger.info("步骤3: 次日涨跌预测")
    logger.info("=" * 60)
    predictor = SentimentPredictor()
    predictions = predictor.predict_all_indices(df)
    target_day = TradingCalendar.get_next_trading_day(today)
    for p in predictions:
        p["predict_date"] = today
        p["target_date"] = target_day
    logger.info("目标交易日: %s", target_day)
    logger.info("预测摘要:\n%s", predictor.generate_summary(predictions))

    # 4. 入库
    logger.info("=" * 60)
    logger.info("步骤4: 数据持久化")
    logger.info("=" * 60)
    db = MySQLManager()
    try:
        n = db.insert_raw_posts(df)
        logger.info("原始帖子写入: %d 条", n)
        db.insert_sentiment_stats(stats_df)
        logger.info("情绪统计写入完成")
        n_pred = db.insert_predictions(predictions)
        logger.info("预测记录写入: %d 条", n_pred)
    except Exception as e:
        logger.warning("数据库写入失败(非致命): %s", str(e)[:200])
    finally:
        db.close()

    # 5. Excel + 推送
    logger.info("=" * 60)
    logger.info("步骤5: Excel 日报 + 推送")
    logger.info("=" * 60)
    excel = generate_excel_report(
        report_date=today,
        posts_df=df,
        stats_df=stats_df,
        predictions=predictions,
        accuracy=None,
    )
    logger.info("Excel: %s", excel)
    send_daily_prediction(predictions, today.strftime("%Y-%m-%d"))
    logger.info("补跑完成 ✅")


if __name__ == "__main__":
    main()
