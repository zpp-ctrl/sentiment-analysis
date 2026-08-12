# -*- coding: utf-8 -*-
"""
============================================================
健康检查 & 自动补跑脚本
============================================================
功能:
  1. 检查今日流水线是否成功运行
  2. 检查数据库中的最新数据日期
  3. 如果漏跑，自动触发补跑
  4. 生成健康状态报告

使用:
  python health_check.py              # 检查状态
  python health_check.py --catch-up   # 自动补跑今天错过的任务
  python health_check.py --report     # 生成详细报告
"""

import os
import sys
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# ★ 修复Windows GBK编码问题（必须在任何print之前）
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 轻量日志（不依赖 module8_scheduler 的 setup_logging）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(PROJECT_ROOT, "logs", "health_check.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("health_check")


def check_pipeline_status() -> dict:
    """检查今日流水线运行状态"""
    today = date.today()
    result = {
        "date": today.isoformat(),
        "pipeline_ran": False,
        "pipeline_success": False,
        "backtest_ran": False,
        "backtest_success": False,
        "db_latest_date": None,
        "db_post_count_today": 0,
        "issues": [],
    }

    # 1. 检查状态文件
    status_file = os.path.join(PROJECT_ROOT, "output", ".pipeline_last_status.txt")
    if os.path.exists(status_file):
        with open(status_file, "r", encoding="utf-8") as f:
            status = f.read().strip()
        result["pipeline_ran"] = True
        result["pipeline_success"] = "success" in status

    backtest_status = os.path.join(PROJECT_ROOT, "output", ".backtest_last_status.txt")
    if os.path.exists(backtest_status):
        with open(backtest_status, "r", encoding="utf-8") as f:
            status = f.read().strip()
        result["backtest_ran"] = True
        result["backtest_success"] = "success" in status

    # 2. 检查数据库中最新的数据
    db_path = os.path.join(PROJECT_ROOT, "output", "financial_sentiment.db")
    if os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            # 今天的数据量
            cur.execute(
                "SELECT COUNT(*) FROM raw_posts WHERE date(collect_time) = ?",
                (today.isoformat(),),
            )
            result["db_post_count_today"] = cur.fetchone()[0]

            # 最新数据日期
            cur.execute("SELECT MAX(date(collect_time)) FROM raw_posts")
            row = cur.fetchone()
            result["db_latest_date"] = row[0] if row else None

            # 检查今天是否有帖子写入
            cur.execute(
                "SELECT COUNT(*) FROM daily_sentiment_stats WHERE stat_date = ?",
                (today.isoformat(),),
            )
            result["stats_count_today"] = cur.fetchone()[0]

            conn.close()
        except Exception as e:
            result["issues"].append(f"数据库检查失败: {e}")

    # 3. 检查Excel日报
    excel_file = os.path.join(
        PROJECT_ROOT, "output", f"{today.strftime('%Y%m%d')}_财经舆情预测日报.xlsx"
    )
    result["excel_exists"] = os.path.exists(excel_file)

    # 4. 判断是否有问题
    if result["db_post_count_today"] == 0:
        result["issues"].append("今天数据库中没有新帖子！")
    if not result["excel_exists"]:
        result["issues"].append("今天Excel日报未生成！")
    if result["db_latest_date"] and result["db_latest_date"] < today.isoformat():
        result["issues"].append(
            f"最新数据日期是 {result['db_latest_date']}，今天({today})尚无数据！"
        )

    result["healthy"] = len(result["issues"]) == 0
    return result


def print_status(result: dict):
    """打印健康状态报告"""
    print()
    print("=" * 60)
    print("  📊 品品财经舆情系统 - 健康状态报告")
    print("=" * 60)
    print(f"  检查日期: {result['date']}")
    print(f"  整体状态: {'🟢 正常' if result['healthy'] else '🔴 异常'}")
    print()

    print("  ── 流水线状态 ──")
    print(f"  主流水线: {'✅ 已运行' if result['pipeline_ran'] else '⚠️  未运行'}")
    print(f"  执行结果: {'✅ 成功' if result['pipeline_success'] else '❌ 失败' if result['pipeline_ran'] else '—'}")
    print(f"  回测任务: {'✅ 已运行' if result['backtest_ran'] else '⚠️  未运行'}")
    print()

    print("  ── 数据状态 ──")
    print(f"  今日帖子数: {result['db_post_count_today']} 条")
    print(f"  今日统计: {'✅ 已生成' if result.get('stats_count_today', 0) > 0 else '❌ 未生成'}")
    print(f"  最新数据日期: {result['db_latest_date'] or '无数据'}")
    print(f"  Excel日报: {'✅ 已生成' if result['excel_exists'] else '❌ 未生成'}")
    print()

    if result["issues"]:
        print("  ── 发现的问题 ──")
        for i, issue in enumerate(result["issues"], 1):
            print(f"  {i}. {issue}")
        print()

    print("=" * 60)
    return result["healthy"]


def run_catch_up():
    """自动补跑今天错过的任务"""
    from module8_scheduler import SchedulerManager

    today = date.today()
    logger.info("=" * 60)
    logger.info("自动补跑模式启动 - 日期: %s", today)
    logger.info("=" * 60)

    # 先检查状态
    result = check_pipeline_status()

    mgr = SchedulerManager()

    # 判断是否需要补跑
    now = datetime.now()

    if result["db_post_count_today"] == 0:
        logger.info("检测到今天无数据，开始补跑主流水线...")
        try:
            mgr.run_once("pipeline")
            logger.info("补跑主流水线完成！")
        except Exception as e:
            logger.error("补跑主流水线失败: %s", str(e))
    else:
        logger.info("今天已有 %d 条数据，跳过主流水线补跑", result["db_post_count_today"])

    # 回测补跑（只在15:35之后）
    from config import BACKTEST_TIME
    backtest_h, backtest_m = map(int, BACKTEST_TIME.split(":"))
    backtest_dt = datetime.now().replace(hour=backtest_h, minute=backtest_m, second=0)
    if now >= backtest_dt and not result["backtest_success"]:
        logger.info("回测时间已过且未成功运行，开始补跑回测...")
        try:
            mgr.run_once("backtest")
            logger.info("补跑回测任务完成！")
        except Exception as e:
            logger.error("补跑回测任务失败: %s", str(e))

    # 最终再检查一次
    logger.info("补跑完成，最终状态检查:")
    final_result = check_pipeline_status()
    print_status(final_result)


def generate_report():
    """生成详细的文本报告"""
    result = check_pipeline_status()
    print_status(result)

    # 输出最近7天的数据统计
    print()
    print("  ── 最近7天数据统计 ──")
    db_path = os.path.join(PROJECT_ROOT, "output", "financial_sentiment.db")
    if os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT date(collect_time) as d, platform, COUNT(*) as cnt
                FROM raw_posts
                WHERE date(collect_time) >= date('now', '-7 days', 'localtime')
                GROUP BY d, platform
                ORDER BY d DESC, platform
            """)
            for row in cur.fetchall():
                print(f"  {row[0]} | {row[1]:12s} | {row[2]:5d} 条")
            cur.execute("""
                SELECT COUNT(*) FROM raw_posts
                WHERE date(collect_time) >= date('now', '-7 days', 'localtime')
            """)
            total = cur.fetchone()[0]
            print(f"  ─────────────────────────────")
            print(f"  近7天总计: {total} 条")
            conn.close()
        except Exception as e:
            print(f"  统计查询失败: {e}")


# ==================== 主入口 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="品品财经舆情系统 - 健康检查工具")
    parser.add_argument("--catch-up", action="store_true", help="自动补跑今天错过的任务")
    parser.add_argument("--report", action="store_true", help="生成详细健康状况报告")
    args = parser.parse_args()

    try:
        if args.catch_up:
            run_catch_up()
        elif args.report:
            generate_report()
        else:
            result = check_pipeline_status()
            healthy = print_status(result)
            if not healthy:
                print("  💡 提示: 运行 python health_check.py --catch-up 自动补跑")
    except KeyboardInterrupt:
        print("\n中断")
    except Exception as e:
        logger.error("健康检查异常: %s", str(e), exc_info=True)
        sys.exit(1)
