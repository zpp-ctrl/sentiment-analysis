# -*- coding: utf-8 -*-
"""
============================================================
每日自动财经舆情分析系统 - 主入口
============================================================

功能:
  1. 每日定时爬取抖音财经博主数据（前1000名, 粉丝>10万）
  2. 区分视频/图文帖子，确保博主全量内容采集（过去24小时）
  3. 多空情感分析 + 情绪指标汇总
  4. 基于情感分析预测四大指数次日涨跌方向
  5. T+1日收盘回测验证预测准确率（上证/沪深300/中证500/中证1000）
  6. 自动生成Excel分析日报

使用方式:
  1. 自动模式（推荐，首次运行即完成部署）:
     python main.py                    # 自动注册Windows定时任务 + 立即执行一次
     python main.py --install          # 仅安装/更新定时任务
     python main.py --status           # 查看定时任务状态

  2. 单次执行模式（测试/手动触发）:
     python main.py --once pipeline    # 仅主流水线
     python main.py --once backtest    # 仅回测
     python main.py --once all         # 全部执行

  3. 守护进程模式（需保持终端开启，不推荐）:
     python main.py --daemon

部署前准备:
  1. pip install -r requirements.txt
  2. 修改 config.py 中的 MySQL 连接信息
  3. 执行 sql/init_db.sql 初始化数据库表
  4. 确保 MySQL 服务已启动且可连接
  5. python module1_account_collector.py --login
"""

import sys
import os

# 确保项目根目录在sys.path中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from module8_scheduler import main

if __name__ == "__main__":
    main()
