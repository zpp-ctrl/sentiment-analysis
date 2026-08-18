# -*- coding: utf-8 -*-
"""
============================================================
模块8: 定时调度主程序 & 日志 & 异常重试机制
============================================================
功能:
  1. 每日定时主流水线全流程自动执行（采集→情感分析→涨跌预测→报表）
  2. 每个交易日15:35 自动回测比对
  3. schedule库本地定时 + Linux crontab部署注释
  4. 统一日志管理 + 异常重试 + 工作日/周末判断
"""

import os
import sys
import time
import logging
import traceback
from datetime import date, datetime, timedelta
from typing import Optional

# ★ 最早执行: 修复Windows GBK编码问题
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import schedule

# 添加项目根目录到sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    MAIN_PIPELINE_TIME,
    BACKTEST_TIME,
    LOGS_DIR,
    OUTPUT_DIR,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    MAX_RETRY_COUNT,
    RETRY_DELAY_SECONDS,
)
from module4_index_data import TradingCalendar

# ==================== 日志初始化 ====================


def setup_logging(log_file_name: Optional[str] = None) -> logging.Logger:
    """
    初始化全局日志系统
    同时输出到控制台和日志文件
    """
    if log_file_name is None:
        log_file_name = f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"

    log_file_path = os.path.join(LOGS_DIR, log_file_name)

    # ★ v5: 修复Windows GBK编码问题
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清除已有handler（避免重复）
    root_logger.handlers.clear()

    # 控制台handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # 文件handler
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)

    logger = logging.getLogger(__name__)
    logger.info("日志系统初始化完成: %s", log_file_path)
    return logger


logger = logging.getLogger(__name__)


# ==================== 重试装饰器 ====================

def with_retry(max_retries: int = MAX_RETRY_COUNT, delay: int = RETRY_DELAY_SECONDS):
    """
    通用重试装饰器（用于流水线关键步骤）
    捕获所有异常，记录日志，自动重试
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    logger.info("[%s] 执行中... (第 %d/%d 次)",
                                func.__name__, attempt, max_retries)
                    result = func(*args, **kwargs)
                    logger.info("[%s] 执行成功", func.__name__)
                    return result
                except Exception as exc:
                    last_exception = exc
                    logger.error(
                        "[%s] 执行失败 (第 %d/%d 次): %s\n%s",
                        func.__name__, attempt, max_retries,
                        str(exc), traceback.format_exc(),
                    )
                    if attempt < max_retries:
                        sleep_time = delay * attempt
                        logger.info("[%s] %d秒后重试...", func.__name__, sleep_time)
                        time.sleep(sleep_time)
            logger.critical(
                "[%s] 已达最大重试次数(%d)，任务失败！",
                func.__name__, max_retries,
            )
            raise last_exception

        return wrapper

    return decorator


# ==================== 主流水线 ====================

class MainPipeline:
    """
    每日主流水线控制器
    执行顺序: 采集 → 清洗分类 → 情绪统计 → 涨跌预测 → 存储 → Excel导出
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def step1_collect_posts(self) -> dict:
        """
        步骤1: 抖音 + 小红书 双平台账号池构建 & 帖子采集
        """
        import pandas as pd
        from module1_account_collector import run_account_and_collect, export_ranked_accounts

        all_posts = []

        # ==================== 步骤1a: 抖音采集 ====================
        self.logger.info("=" * 60)
        self.logger.info(">>> [步骤1a] 抖音账号池构建 & 帖子采集 <<<")
        self.logger.info("=" * 60)

        dy_posts_df = run_account_and_collect()
        dy_count = len(dy_posts_df) if dy_posts_df is not None else 0
        self.logger.info("抖音采集完成: %d 条帖子", dy_count)
        if dy_posts_df is not None and len(dy_posts_df) > 0:
            all_posts.append(dy_posts_df)

        # 导出抖音前1000名排名
        self.logger.info("=" * 60)
        self.logger.info(">>> [步骤1b] 抖音账号排名 TOP1000 <<<")
        self.logger.info("=" * 60)
        try:
            rank_file = export_ranked_accounts()
            self.logger.info("排名文件: %s", rank_file)
        except Exception as e:
            self.logger.warning("抖音账号排名失败(非致命): %s", str(e)[:120])

        # ==================== 步骤1c: 小红书采集 (已禁用) ====================
        xhs_count = 0
        self.logger.info(">>> [步骤1c] 小红书采集: 已禁用(仅使用抖音数据) <<<")

        # ==================== 合并双平台数据 ====================
        if all_posts:
            posts_df = pd.concat(all_posts, ignore_index=True)
            self.logger.info("平台合并: 共 %d 条帖子 (抖音=%d, 小红书=已禁用)",
                             len(posts_df), dy_count)
        else:
            posts_df = pd.DataFrame()
            self.logger.warning("双平台采集均为空!")

        return {"posts_df": posts_df}

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def step1_5_video_extract(self, context: dict) -> dict:
        """
        步骤1.5: 视频内容提取
        对 post_type='video' 的帖子，下载视频并提取音频文字+画面描述
        结果追加到 post_content，为后续情感分析提供更丰富的文本
        失败时自动回退到仅文字分析（不阻断流水线）
        """
        from config import VIDEO_EXTRACTION_ENABLED
        self.logger.info("=" * 60)
        self.logger.info(">>> [步骤1.5] 视频内容提取 <<<")
        self.logger.info("=" * 60)

        if not VIDEO_EXTRACTION_ENABLED:
            self.logger.info("视频提取功能已关闭(VIDEO_EXTRACTION_ENABLED=False)，跳过")
            return context

        posts_df = context.get("posts_df")
        if posts_df is None or len(posts_df) == 0:
            self.logger.warning("无帖子数据，跳过视频提取")
            return context

        # 统计视频帖子数量
        video_count = 0
        if "post_type" in posts_df.columns:
            video_count = (posts_df["post_type"] == "video").sum()
        if video_count == 0:
            self.logger.info("无视频帖子，跳过视频提取")
            return context

        self.logger.info("发现 %d 条视频帖子，开始提取内容...", video_count)

        try:
            from module_video_content_extractor import VideoContentExtractor
            extractor = VideoContentExtractor()
            enriched_df = extractor.enrich_dataframe(posts_df)
            context["posts_df"] = enriched_df
            context["_video_extraction_stats"] = extractor.get_stats()
        except Exception as e:
            self.logger.warning("视频内容提取失败(非致命)，回退到纯文本分析: %s", str(e)[:200])
            # 不修改 context，继续使用原始 posts_df
            context["_video_extraction_stats"] = {"error": str(e)[:200]}

        return context

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def step2_sentiment_classify(self, context: dict) -> dict:
        """
        步骤2: 文本清洗 + 情感分类
        支持词典模式(LLM_ENABLED=False) 和 LLM模式(LLM_ENABLED=True)
        """
        from config import LLM_ENABLED
        self.logger.info("=" * 60)
        if LLM_ENABLED:
            self.logger.info(">>> [步骤2] 文本清洗 & LLM情感分类 (Claude API) <<<")
        else:
            self.logger.info(">>> [步骤2] 文本清洗 & 多空情感分类 (关键词词典) <<<")
        self.logger.info("=" * 60)

        posts_df = context.get("posts_df")
        if posts_df is None or len(posts_df) == 0:
            self.logger.warning("无帖子数据，创建空DataFrame")
            import pandas as pd
            posts_df = pd.DataFrame()

        if LLM_ENABLED:
            from module2_text_sentiment import classify_batch_llm
            classified_df = classify_batch_llm(posts_df)
        else:
            from module2_text_sentiment import classify_batch
            classified_df = classify_batch(posts_df)

        context["posts_df"] = classified_df
        return context

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def step3_sentiment_stats(self, context: dict) -> dict:
        """
        步骤3: 情绪指标汇总统计
        """
        from module3_sentiment_aggregator import run_sentiment_aggregation
        self.logger.info("=" * 60)
        self.logger.info(">>> [步骤3] 情绪指标汇总统计 <<<")
        self.logger.info("=" * 60)

        today = date.today()
        posts_df = context.get("posts_df")
        stats_df = run_sentiment_aggregation(posts_df, stat_date=today)
        context["stats_df"] = stats_df
        context["stat_date"] = today
        return context

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def step4_sentiment_predict(self, context: dict) -> dict:
        """
        ★ 步骤4: 基于情感分析预测四大指数当日涨跌方向
        原理: 粉丝加权 → 指数关键词匹配 → 看多/看空得分 → 判定涨跌
        注意: 仅预测涨跌方向，不预测具体指数值
        """
        self.logger.info("=" * 60)
        self.logger.info(">>> [步骤4] 情感分析涨跌预测 <<<")
        self.logger.info("=" * 60)

        from module5_sentiment_predictor import SentimentPredictor

        posts_df = context.get("posts_df")
        today = context.get("stat_date", date.today())

        predictor = SentimentPredictor()

        if posts_df is None or len(posts_df) == 0:
            self.logger.warning("无帖子数据，跳过预测")
            context["predictions"] = []
            return context

        # 基于情感分析预测
        predictions = predictor.predict_all_indices(posts_df)

        # 添加日期信息 ★ 预测次日指数涨跌
        target_day = TradingCalendar.get_next_trading_day(today)  # 预测下一个交易日（明天）
        for pred in predictions:
            pred["predict_date"] = today
            pred["target_date"] = target_day

        # 输出摘要
        summary = predictor.generate_summary(predictions)
        self.logger.info("预测摘要:\n%s", summary)

        context["predictions"] = predictions
        self.logger.info("预测完成: %d 个指数", len(predictions))
        return context

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def step5_save_to_db(self, context: dict) -> dict:
        """
        步骤5: 数据持久化到MySQL/SQLite
        ★ v7: 数据库连接失败不阻断流程，数据已在CSV中
        """
        from module7_data_storage import MySQLManager
        self.logger.info("=" * 60)
        self.logger.info(">>> [步骤5] 数据持久化 <<<")
        self.logger.info("=" * 60)

        try:
            db = MySQLManager()
            try:
                # 写入原始帖子
                posts_df = context.get("posts_df")
                if posts_df is not None and len(posts_df) > 0:
                    n = db.insert_raw_posts(posts_df)
                    self.logger.info("原始帖子写入: %d 条", n)

                # 写入情绪统计
                stats_df = context.get("stats_df")
                if stats_df is not None and len(stats_df) > 0:
                    db.insert_sentiment_stats(stats_df)
                    self.logger.info("情绪统计写入完成")

                # 写入预测记录
                predictions = context.get("predictions", [])
                if predictions:
                    db.insert_predictions(predictions)
                    self.logger.info("预测记录写入: %d 条", len(predictions))

                self.logger.info("数据持久化完成")
            finally:
                db.close()
        except Exception as e:
            self.logger.warning("=" * 60)
            self.logger.warning("⚠️ 数据库写入失败(非致命): %s", str(e)[:120])
            self.logger.warning("数据已保存到CSV文件(output/目录)")
            self.logger.warning("如需MySQL: 启动MySQL服务 -> 执行sql/init_db.sql -> 重新运行")
            self.logger.warning("=" * 60)
        return context

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def step6_export_excel(self, context: dict) -> str:
        """
        步骤6: 导出Excel日报
        """
        from module7_data_storage import generate_excel_report
        self.logger.info("=" * 60)
        self.logger.info(">>> [步骤6] Excel日报导出 <<<")
        self.logger.info("=" * 60)

        today = context.get("stat_date", date.today())
        posts_df = context.get("posts_df")
        stats_df = context.get("stats_df")
        predictions = context.get("predictions", [])

        file_path = generate_excel_report(
            report_date=today,
            posts_df=posts_df,
            stats_df=stats_df,
            predictions=predictions,
            accuracy=None,  # 回测报告在回测任务中单独生成
        )
        context["excel_path"] = file_path
        return context

    def run(self):
        """
        执行完整主流水线
        """
        start_time = datetime.now()
        self.logger.info("")
        self.logger.info("*" * 60)
        self.logger.info("  品品每日财经舆情预测 - 主流水线启动")
        self.logger.info("  启动时间: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
        self.logger.info("  日期: %s | 交易日: %s",
                         date.today(), "是" if TradingCalendar.is_trading_day() else "否")
        self.logger.info("*" * 60)

        # ★ 非交易日跳过（周末/节假日不爬取、不预测）
        if not TradingCalendar.is_trading_day():
            self.logger.info("今日非交易日（周末/节假日），跳过主流水线（不爬取、不预测）")
            return {}

        context = {}

        try:
            # 步骤1: 采集
            context = self.step1_collect_posts()

            # ★ 步骤1.5: 视频内容提取（音频转录 + 画面分析）
            context = self.step1_5_video_extract(context)

            # 步骤2: 文本清洗 + 情感分类
            context = self.step2_sentiment_classify(context)

            # 步骤3: 情绪统计
            context = self.step3_sentiment_stats(context)

            # ★ 步骤4: 情感分析预测四大指数涨跌
            context = self.step4_sentiment_predict(context)

            # 步骤5: 数据库写入
            context = self.step5_save_to_db(context)

            # 步骤6: Excel日报
            context = self.step6_export_excel(context)

        except Exception as exc:
            self.logger.critical("主流水线执行失败: %s\n%s", str(exc), traceback.format_exc())
            raise

        elapsed = (datetime.now() - start_time).total_seconds()
        self.logger.info("*" * 60)
        self.logger.info("  主流水线执行完毕！耗时: %.1f 秒", elapsed)
        if context.get("excel_path"):
            self.logger.info("  Excel日报: %s", context["excel_path"])
        self.logger.info("*" * 60)

        # ★ 预测完成后推送到企业微信
        predictions = context.get("predictions", [])
        if predictions:
            try:
                from module_notify import send_daily_prediction
                stat_date = context.get("stat_date", "").strftime("%Y-%m-%d") if hasattr(context.get("stat_date", ""), "strftime") else str(context.get("stat_date", ""))
                send_daily_prediction(predictions, stat_date)
            except Exception as e:
                self.logger.warning("推送通知失败: %s", str(e))

        return context


# ==================== 回测任务 ====================

class BacktestTask:
    """
    每日回测任务
    交易日15:35执行: 拉取真实指数 → 匹配预测 → 统计准确率 → 更新DB
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    @with_retry(max_retries=MAX_RETRY_COUNT)
    def run(self):
        """执行回测任务"""
        from module6_backtest import BacktestEngine
        from module7_data_storage import MySQLManager, generate_excel_report

        start_time = datetime.now()
        self.logger.info("")
        self.logger.info("*" * 60)
        self.logger.info("  品品每日财经舆情预测 - 回测任务启动")
        self.logger.info("  启动时间: %s", start_time.strftime("%Y-%m-%d %H:%M:%S"))
        self.logger.info("*" * 60)

        if not TradingCalendar.is_trading_day():
            self.logger.info("今日非交易日，跳过回测任务")
            return

        db = MySQLManager()
        try:
            # 1. 获取所有待回测记录
            pending_df = db.get_all_pending_backtest()
            if pending_df is None or len(pending_df) == 0:
                self.logger.info("无待回测记录，任务结束")
                return

            self.logger.info("发现 %d 条待回测预测记录", len(pending_df))

            # 2. 按 predict_date 分组回测
            engine = BacktestEngine()
            all_results = []
            all_accuracy = {}

            for pred_date, group in pending_df.groupby("predict_date"):
                target_date = pred_date + timedelta(days=1)
                # 找下一个交易日
                while not TradingCalendar.is_trading_day(target_date):
                    target_date += timedelta(days=1)

                self.logger.info("回测: predict_date=%s, target_date=%s",
                                 pred_date, target_date)

                backtest_df, accuracy = engine.run_full_backtest(group, target_date)
                all_results.append(backtest_df)
                all_accuracy = accuracy  # 取最后一个

                # 3. 更新DB
                db.update_backtest_results(backtest_df)

            # 4. 生成回测Excel报告
            if all_accuracy:
                import pandas as pd
                report_date = date.today()
                file_path = generate_excel_report(
                    report_date=report_date,
                    posts_df=pd.DataFrame(),  # 回测报告不需要帖子明细
                    stats_df=pd.DataFrame(),   # 回测报告不需要情绪统计
                    predictions=pending_df.to_dict("records"),
                    accuracy=all_accuracy,
                )
                self.logger.info("回测Excel报告已导出: %s", file_path)

        except Exception as exc:
            self.logger.critical("回测任务执行失败: %s\n%s", str(exc), traceback.format_exc())
            raise
        finally:
            db.close()

        elapsed = (datetime.now() - start_time).total_seconds()
        self.logger.info("回测任务完成！耗时: %.1f 秒", elapsed)


# ==================== Windows 任务计划程序集成 ====================

def _get_project_root():
    """获取项目根目录（兼容打包后路径）"""
    return os.path.dirname(os.path.abspath(__file__))


def _get_python_exe():
    """获取虚拟环境中的 Python 解释器路径"""
    venv_python = os.path.join(_get_project_root(), ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    # 回退到系统 Python
    return sys.executable


def _check_windows_task(task_name: str) -> bool:
    """
    检查 Windows 任务计划程序中是否存在指定任务

    Returns:
        True 表示任务存在
    """
    import subprocess
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", task_name],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def install_windows_tasks():
    """
    自动注册 Windows 任务计划程序定时任务

    这样不需要一直运行 python main.py，系统会在配置的时间自动触发。
    需要管理员权限才能注册成功。

    Returns:
        (success_count, total_count): 成功数和总数
    """
    import subprocess

    project_dir = _get_project_root()
    python_exe = _get_python_exe()
    pipeline_bat = os.path.join(project_dir, "run_pipeline.bat")
    backtest_bat = os.path.join(project_dir, "run_backtest.bat")

    tasks = [
        {
            "name": "FinancePipeline",
            "tr": f'"{pipeline_bat}"',
            "st": MAIN_PIPELINE_TIME,
            "description": "每日主流水线(采集+分析+预测+日报)",
        },
        {
            "name": "FinanceBacktest",
            "tr": f'"{backtest_bat}"',
            "st": BACKTEST_TIME,
            "description": "每日回测验证",
        },
    ]

    success_count = 0
    for task in tasks:
        # 先删除旧任务（如果存在）
        subprocess.run(
            ["schtasks", "/delete", "/tn", task["name"], "/f"],
            capture_output=True, text=True, timeout=10,
        )
        # 创建新任务 (使用XML方式以支持更多设置)
        task_xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T{task["st"]}:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>true</WakeToRun>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{task["tr"]}</Command>
      <WorkingDirectory>{project_dir}</WorkingDirectory>
    </Exec>
  </Actions>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
</Task>'''
        xml_path = os.path.join(OUTPUT_DIR, f"{task['name']}.xml")
        with open(xml_path, "w", encoding="utf-16") as f:
            f.write(task_xml)

        result = subprocess.run(
            ["schtasks", "/create", "/tn", task["name"], "/xml", xml_path, "/f"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            success_count += 1

    return success_count, len(tasks)


def get_windows_task_status():
    """
    获取 Windows 定时任务状态摘要

    Returns:
        dict: 包含各任务存在与否的状态
    """
    pipeline_exists = _check_windows_task("FinancePipeline")
    backtest_exists = _check_windows_task("FinanceBacktest")
    return {
        "pipeline": pipeline_exists,
        "backtest": backtest_exists,
        "all_ok": pipeline_exists and backtest_exists,
    }


# ==================== 调度管理器 ====================

class SchedulerManager:
    """
    定时调度管理器
    支持两种模式:
      1. Windows 任务计划程序（推荐，不需要保持进程运行）
      2. schedule 库进程内循环（需要保持终端开启）
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.pipeline = MainPipeline()
        self.backtest = BacktestTask()
        self.running = True

    def _job_main_pipeline(self):
        """主流水线定时任务包装"""
        try:
            self.pipeline.run()
        except Exception as exc:
            self.logger.critical("主流水线异常退出: %s", str(exc))

    def _job_backtest(self):
        """回测任务定时任务包装"""
        try:
            self.backtest.run()
        except Exception as exc:
            self.logger.critical("回测任务异常退出: %s", str(exc))

    def setup_schedule(self):
        """配置 schedule 库定时任务"""
        schedule.every().day.at(MAIN_PIPELINE_TIME).do(self._job_main_pipeline)
        self.logger.info("已注册: 每日 %s 主流水线", MAIN_PIPELINE_TIME)

        schedule.every().day.at(BACKTEST_TIME).do(self._job_backtest)
        self.logger.info("已注册: 每日 %s 回测任务", BACKTEST_TIME)

    def _catch_up_missed_jobs(self):
        """
        启动时补跑：检查今天是否有已错过调度时间但尚未执行的任务。

        场景：调度器启动晚于配置的任务时间（如 10:07 启动但 pipeline 定在 10:00），
        schedule 库不会补跑已过时间点，这里主动检测并立即执行一次。
        各任务内部自带交易日判断，非交易日会自动跳过。
        """
        from datetime import time as dt_time

        # ★ 非交易日跳过补跑检查
        if not TradingCalendar.is_trading_day():
            self.logger.info("今日非交易日（周末/节假日），跳过所有补跑检查")
            return

        now = datetime.now()
        current_time = now.time()

        # --- 主流水线 ---
        pipeline_h, pipeline_m = map(int, MAIN_PIPELINE_TIME.split(":"))
        pipeline_time = dt_time(pipeline_h, pipeline_m)

        if current_time >= pipeline_time:
            self.logger.info(
                "★ 启动补跑: 今日主流水线时间 %s 已过（当前 %s），立即执行",
                MAIN_PIPELINE_TIME, now.strftime("%H:%M:%S"),
            )
            self._job_main_pipeline()
        else:
            self.logger.info(
                "主流水线将在今日 %s 触发（当前 %s）",
                MAIN_PIPELINE_TIME, now.strftime("%H:%M:%S"),
            )

        # --- 回测任务 ---
        backtest_h, backtest_m = map(int, BACKTEST_TIME.split(":"))
        backtest_time = dt_time(backtest_h, backtest_m)

        if current_time >= backtest_time:
            self.logger.info(
                "★ 启动补跑: 今日回测任务时间 %s 已过（当前 %s），立即执行",
                BACKTEST_TIME, now.strftime("%H:%M:%S"),
            )
            self._job_backtest()
        else:
            self.logger.info(
                "回测任务将在今日 %s 触发（当前 %s）",
                BACKTEST_TIME, now.strftime("%H:%M:%S"),
            )

    def run_loop(self):
        """启动 schedule 库调度循环（需要保持进程运行）"""
        self.setup_schedule()
        self.logger.info("定时调度器已启动，等待任务触发...")
        self.logger.info("按 Ctrl+C 停止调度器")

        # ★ 启动时补跑今日已错过的任务
        self._catch_up_missed_jobs()

        try:
            while self.running:
                schedule.run_pending()
                time.sleep(30)  # 每30秒检查一次
        except KeyboardInterrupt:
            self.logger.info("收到中断信号，调度器停止")
            self.running = False

    def run_once(self, task_type: str = "all"):
        """
        立即执行一次（手动触发模式）

        Args:
            task_type: "pipeline" / "backtest" / "all"
        """
        if task_type in ("pipeline", "all"):
            self._job_main_pipeline()
        if task_type in ("backtest", "all"):
            self._job_backtest()


# ==================== 程序入口 ====================

def main():
    """主函数入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="品品每日自动财经舆情分析系统 - 基于抖音财经大V情感分析预测四大指数涨跌",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py                    # 自动设置定时任务 + 立即执行一次流水线
  python main.py --once pipeline    # 立即执行一次主流水线
  python main.py --once backtest    # 立即执行一次回测
  python main.py --once all         # 立即执行完整流水线+回测
  python main.py --install          # 安装/更新 Windows 定时任务
  python main.py --status           # 查看定时任务状态
  python main.py --daemon           # 启动进程内调度循环（需保持终端开启）
        """,
    )
    parser.add_argument(
        "--once", choices=["pipeline", "backtest", "all"],
        help="立即执行一次指定任务，然后退出",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="安装/更新 Windows 定时任务（需要管理员权限）",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="查看 Windows 定时任务状态",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="启动进程内调度循环（需保持终端开启，不推荐）",
    )
    args = parser.parse_args()

    # 初始化日志
    setup_logging()
    logger = logging.getLogger("main")

    # 检查依赖
    try:
        import pandas
        import pymysql
        import schedule
        import openpyxl
    except ImportError as exc:
        logger.error("缺少依赖库: %s", str(exc))
        logger.error("请执行: pip install -r requirements.txt")
        sys.exit(1)

    logger.info("所有依赖库检查通过")

    mgr = SchedulerManager()

    # ---- 命令分发 ----

    if args.once:
        # 单次执行模式
        logger.info("单次执行模式: %s", args.once)
        mgr.run_once(args.once)
        return

    if args.status:
        # 查看定时任务状态
        _print_task_status(logger)
        return

    if args.install:
        # 安装 Windows 定时任务
        _do_install_tasks(logger)
        return

    if args.daemon:
        # 进程内调度循环（旧方式，需保持终端开启）
        logger.info("定时调度模式启动（进程内循环）")
        logger.warning("⚠️  此模式需要保持终端24小时开启！")
        logger.warning("⚠️  推荐使用 Windows 任务计划程序: python main.py --install")
        mgr.run_loop()
        return

    # ---- 默认模式：自动检测 & 安装 Windows 定时任务 ----
    logger.info("")
    logger.info("=" * 60)
    logger.info("  品品每日财经舆情预测系统")
    logger.info("=" * 60)
    logger.info("")

    # 检查 Windows 任务计划程序状态
    task_status = get_windows_task_status()

    if task_status["all_ok"]:
        logger.info("✅ Windows 定时任务已就绪：")
        logger.info("   📊 主流水线: 每日 %s (FinancePipeline)", MAIN_PIPELINE_TIME)
        logger.info("   📈 回测验证: 每日 %s (FinanceBacktest)", BACKTEST_TIME)
        logger.info("")
        logger.info("💡 无需保持终端开启，系统会按时自动执行。")
        logger.info("💡 如需手动执行: python main.py --once pipeline")
        logger.info("💡 如需修改时间: 编辑 config.py -> 重新运行 python main.py --install")
        logger.info("")

        # 检查今天是否需要补跑
        _check_and_offer_catch_up(mgr, logger)

    else:
        logger.warning("⚠️  Windows 定时任务未配置或部分缺失：")
        logger.info("   Pipeline: %s", "✅" if task_status["pipeline"] else "❌ 未注册")
        logger.info("   Backtest: %s", "✅" if task_status["backtest"] else "❌ 未注册")
        logger.info("")

        # 自动尝试安装
        logger.info("🔧 正在自动注册 Windows 定时任务...")
        _do_install_tasks(logger)

        # 安装后立即执行一次流水线
        logger.info("")
        logger.info("🚀 首次安装，立即执行一次主流水线...")
        mgr.run_once("pipeline")


def _print_task_status(logger):
    """打印 Windows 定时任务状态"""
    task_status = get_windows_task_status()
    logger.info("")
    logger.info("=" * 50)
    logger.info("  Windows 定时任务状态")
    logger.info("=" * 50)
    logger.info("  📊 主流水线 (%s): %s",
                MAIN_PIPELINE_TIME,
                "✅ 已注册" if task_status["pipeline"] else "❌ 未注册")
    logger.info("  📈 回测验证 (%s): %s",
                BACKTEST_TIME,
                "✅ 已注册" if task_status["backtest"] else "❌ 未注册")
    logger.info("")
    if task_status["all_ok"]:
        logger.info("  ✅ 所有任务就绪，系统会按时自动执行。")
    else:
        logger.info("  ⚠️  任务不完整，运行以下命令修复：")
        logger.info("     python main.py --install")
    logger.info("")


def _do_install_tasks(logger):
    """执行 Windows 定时任务安装"""
    import ctypes

    # 检查管理员权限
    is_admin = False
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        pass

    if not is_admin:
        logger.warning("⚠️  当前未以管理员身份运行。")
        logger.warning("⚠️  注册系统定时任务需要管理员权限。")
        logger.info("")
        logger.info("请选择以下方式之一：")
        logger.info("  1. 右键 PowerShell/CMD → 以管理员身份运行 → 执行:")
        logger.info("     python main.py --install")
        logger.info("  2. 或以管理员身份运行批处理文件:")
        logger.info("     setup_tasks.bat")
        logger.info("  3. 手动打开'任务计划程序'(taskschd.msc)创建任务")
        logger.info("")
        logger.info("📋 需要创建的任务信息：")
        logger.info("   任务1: FinancePipeline")
        logger.info("      程序: %s", os.path.join(_get_project_root(), "run_pipeline.bat"))
        logger.info("      时间: 每日 %s", MAIN_PIPELINE_TIME)
        logger.info("   任务2: FinanceBacktest")
        logger.info("      程序: %s", os.path.join(_get_project_root(), "run_backtest.bat"))
        logger.info("      时间: 每日 %s", BACKTEST_TIME)
        return

    logger.info("🔧 正在注册 Windows 定时任务...")
    success, total = install_windows_tasks()

    if success == total:
        logger.info("✅ Windows 定时任务注册成功！(%d/%d)", success, total)
        logger.info("   📊 主流水线: 每日 %s", MAIN_PIPELINE_TIME)
        logger.info("   📈 回测验证: 每日 %s", BACKTEST_TIME)
        logger.info("")
        logger.info("💡 系统会按时自动执行，无需保持任何窗口开启。")
    else:
        logger.error("❌ 注册部分失败: %d/%d 成功", success, total)
        logger.error("   请尝试以管理员身份运行 setup_tasks.bat")


def _check_and_offer_catch_up(mgr, logger):
    """检查今天是否需要补跑已错过的任务"""
    from datetime import time as dt_time

    # ★ 非交易日跳过补跑检查
    if not TradingCalendar.is_trading_day():
        logger.info("📅 今日非交易日（周末/节假日），跳过补跑检查。")
        return

    now = datetime.now()
    current_time = now.time()

    pipeline_h, pipeline_m = map(int, MAIN_PIPELINE_TIME.split(":"))
    pipeline_time = dt_time(pipeline_h, pipeline_m)

    if current_time >= pipeline_time:
        # 检查今天是否已经执行过（简单判断：看output目录是否有今天的文件）
        today_str = date.today().strftime("%Y%m%d")
        output_dir = os.path.join(_get_project_root(), "output")
        today_files = []
        if os.path.exists(output_dir):
            today_files = [f for f in os.listdir(output_dir)
                           if today_str in f and f.endswith(".xlsx")]
        if not today_files:
            logger.info("⏰ 今日 %s 已过，且未找到今天的日报文件。", MAIN_PIPELINE_TIME)
            logger.info("🚀 自动补跑今日主流水线...")
            mgr.run_once("pipeline")
        else:
            logger.info("✅ 今日日报已存在，跳过补跑。")


if __name__ == "__main__":
    main()


# ==================== Linux crontab 部署注释 ====================
#
# 如果使用Linux crontab替代schedule库，配置如下:
#
# # 编辑 crontab: crontab -e
# # 添加以下两行:
#
# # 每日18:00 执行主流水线
# 0 18 * * * cd /path/to/PythonProject24 && /usr/bin/python3 main.py --once pipeline >> /path/to/logs/cron_pipeline.log 2>&1
#
# # 每个交易日15:35 执行回测任务
# 35 15 * * 1-5 cd /path/to/PythonProject24 && /usr/bin/python3 main.py --once backtest >> /path/to/logs/cron_backtest.log 2>&1
#
# # crontab环境变量（如需要）
# # PYTHONPATH=/path/to/PythonProject24
#
# ==================== systemd 服务部署注释 ====================
#
# 也可以使用systemd timer实现更可靠的定时调度:
#
# /etc/systemd/system/finance-pipeline.service:
# [Unit]
# Description=品品财经舆情预测主流水线
# After=network.target mysql.service
#
# [Service]
# Type=oneshot
# User=your_user
# WorkingDirectory=/path/to/PythonProject24
# ExecStart=/usr/bin/python3 main.py --once pipeline
# StandardOutput=append:/path/to/logs/pipeline.log
# StandardError=append:/path/to/logs/pipeline_error.log
#
# /etc/systemd/system/finance-pipeline.timer:
# [Unit]
# Description=每日18:00触发财经舆情预测流水线
#
# [Timer]
# OnCalendar=*-*-* 18:00:00
# Persistent=true
#
# [Install]
# WantedBy=timers.target
#
# 启用: systemctl enable finance-pipeline.timer && systemctl start finance-pipeline.timer