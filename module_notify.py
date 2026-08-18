# -*- coding: utf-8 -*-
"""
============================================================
模块: 预测结果推送（支持多渠道）
============================================================
1. Server酱 → 个人微信 (推荐，无需电脑开机)
2. 邮箱 → QQ邮箱/手机邮箱提醒
3. 本地文件 → Workbuddy 读取后推微信
============================================================
"""

import logging
import os
import smtplib
import requests
from email.mime.text import MIMEText
from datetime import datetime

logger = logging.getLogger(__name__)

# ==================== 渠道1: Server酱（推荐） ====================
# 获取: https://sct.ftqq.com/ 微信扫码 → 复制 SendKey
SERVER_KEY = "SCT394741TSkfQEEfVocAbKbtKzgy3chMo"

# ==================== 渠道2: 邮箱推送 ====================
# 用QQ邮箱发送到自己的QQ邮箱，手机QQ邮箱APP会实时提醒
EMAIL_ENABLED = False  # True=启用
EMAIL_SENDER = "你的QQ号@qq.com"
EMAIL_PASSWORD = "QQ邮箱授权码"  # QQ邮箱设置→账户→POP3/SMTP→生成授权码
EMAIL_RECEIVER = "你的QQ号@qq.com"

# ==================== 渠道3: Workbuddy 本地文件 ====================
# 流水线结束后将结果写入此文件，Workbuddy 可读取后推送到微信
WORKBUDDY_OUTPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "output", "latest_prediction.md"
)

# ==================== 推送函数 ====================

def _build_markdown(predictions: list, stat_date: str) -> str:
    """构建预测结果的 Markdown 文本"""
    dir_map = {"up": "🟢看涨", "slightly_up": "🟢偏多", "slightly_down": "🔴偏空", "down": "🔴看跌", "neutral": "⚪中性"}
    up_count = sum(1 for p in predictions if p.get("predict_direction") in ("up", "slightly_up"))
    down_count = sum(1 for p in predictions if p.get("predict_direction") in ("down", "slightly_down"))
    neutral_count = len(predictions) - up_count - down_count

    lines = [
        f"## 📊 {stat_date} 品品财经舆情预测",
        "",
        "| 指数 | 方向 | 置信度 |",
        "|------|------|--------|",
    ]
    for p in predictions:
        d = p.get("predict_direction", "neutral")
        lines.append(f"| {p['index_name']} | {dir_map.get(d, d)} | {p.get('confidence', 0):.0%} |")

    lines += [
        "",
        f"看涨:{up_count} | 看跌:{down_count} | 中性:{neutral_count}",
        f"推送: {datetime.now().strftime('%m-%d %H:%M')}",
    ]
    return "\n".join(lines)


def send_server_chan(predictions: list, stat_date: str) -> bool:
    """Server酱推送到微信"""
    if "YOUR_SEND_KEY_HERE" in SERVER_KEY:
        logger.info("Server酱未配置，跳过")
        return False
    try:
        content = _build_markdown(predictions, stat_date)
        up_count = sum(1 for p in predictions if p.get("predict_direction") == "up")
        down_count = sum(1 for p in predictions if p.get("predict_direction") == "down")
        r = requests.post(
            f"https://sctapi.ftqq.com/{SERVER_KEY}.send",
            data={"title": f"📊 {stat_date} 预测: 看涨{up_count} 看跌{down_count}", "desp": content},
            timeout=10,
        )
        if r.json().get("code") == 0:
            logger.info("Server酱 → 微信 推送成功")
            return True
        logger.error("Server酱失败: %s", r.json())
        return False
    except Exception as e:
        logger.error("Server酱异常: %s", e)
        return False


def send_email(predictions: list, stat_date: str) -> bool:
    """邮箱推送"""
    if not EMAIL_ENABLED:
        logger.info("邮箱推送未启用，跳过")
        return False
    try:
        content = _build_markdown(predictions, stat_date)
        msg = MIMEText(content, "html", "utf-8")
        msg["Subject"] = f"📊 {stat_date} 财经预测日报"
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER

        with smtplib.SMTP_SSL("smtp.qq.com", 465) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.sendmail(EMAIL_SENDER, [EMAIL_RECEIVER], msg.as_string())
        logger.info("邮箱推送成功 → %s", EMAIL_RECEIVER)
        return True
    except Exception as e:
        logger.error("邮箱推送异常: %s", e)
        return False


def save_workbuddy_file(predictions: list, stat_date: str) -> bool:
    """保存预测结果到文件，供 Workbuddy 读取"""
    try:
        content = _build_markdown(predictions, stat_date)
        os.makedirs(os.path.dirname(WORKBUDDY_OUTPUT), exist_ok=True)
        with open(WORKBUDDY_OUTPUT, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Workbuddy文件已保存: %s", WORKBUDDY_OUTPUT)
        return True
    except Exception as e:
        logger.error("Workbuddy文件写入异常: %s", e)
        return False


# ==================== 统一入口 ====================

def send_daily_prediction(predictions: list, stat_date: str = None) -> bool:
    """推送预测结果到所有已配置的渠道"""
    if not predictions:
        logger.warning("无预测数据，跳过推送")
        return False

    if stat_date is None:
        stat_date = datetime.now().strftime("%Y-%m-%d")

    import requests  # 延迟导入

    results = []
    results.append(("Server酱", send_server_chan(predictions, stat_date)))
    results.append(("邮箱", send_email(predictions, stat_date)))
    results.append(("Workbuddy文件", save_workbuddy_file(predictions, stat_date)))

    success = [name for name, ok in results if ok]
    logger.info("推送完成: %s", ", ".join(success) if success else "全部跳过")
    return len(success) > 0
