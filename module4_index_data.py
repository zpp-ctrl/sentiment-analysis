# -*- coding: utf-8 -*-
"""
============================================================
模块4: 指数行情数据获取（akshare免费接口）
============================================================
功能:
  1. 获取4大宽基指数日线行情数据
  2. 提供回测所需的实际涨跌幅数据
  3. 交易日历辅助工具
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, Optional

import pandas as pd
import numpy as np

from config import (
    INDEX_CODE_MAP,
    MAX_RETRY_COUNT,
    RETRY_DELAY_SECONDS,
)

logger = logging.getLogger(__name__)

# 尝试导入 akshare（免费行情数据）
try:
    import akshare as ak
    _HAS_AKSHARE = True
    logger.info("akshare 导入成功，将使用免费行情数据")
except ImportError:
    _HAS_AKSHARE = False
    logger.warning("akshare未安装，将使用模拟数据")

# akshare 指数代码映射
_AKSHARE_SYMBOL_MAP = {
    "000001.SH": "sh000001",   # 上证指数
    "000300.SH": "sh000300",   # 沪深300
    "000905.SH": "sh000905",   # 中证500
    "000852.SH": "sh000852",   # 中证1000
}


class IndexDataProvider:
    """
    指数行情数据提供者
    数据源: akshare（免费） > 模拟数据
    """

    def __init__(self):
        logger.info("IndexDataProvider初始化完成（数据源: akshare）")

    def _get_from_akshare(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """
        从 akshare 获取指数日线数据并转换为 Tushare 兼容格式

        Args:
            index_code: 如 "000001.SH"
            start_date: "YYYYMMDD"
            end_date: "YYYYMMDD"

        Returns:
            DataFrame with columns:
              trade_date, open, high, low, close, pre_close,
              pct_chg, vol, amount
        """
        if not _HAS_AKSHARE:
            return None

        symbol = _AKSHARE_SYMBOL_MAP.get(index_code)
        if not symbol:
            logger.warning("akshare 不支持指数代码: %s", index_code)
            return None

        try:
            df_raw = ak.stock_zh_index_daily(symbol=symbol)
            if df_raw is None or len(df_raw) == 0:
                return None

            df = df_raw.copy()
            # 统一列名
            df.rename(columns={
                "date": "trade_date",
                "volume": "vol",
            }, inplace=True)

            # ★ 日期格式统一: datetime.date → "YYYYMMDD" 字符串
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")

            # 确保数值类型
            for col in ["open", "high", "low", "close", "vol"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # 按日期筛选（在计算 pre_close 之前，因为只需要筛选后的数据）
            df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]

            if len(df) == 0:
                return None

            # 计算 pre_close（前一日的收盘价）
            df["pre_close"] = df["close"].shift(1)

            # 计算涨跌幅 pct_chg（百分比）
            df["pct_chg"] = ((df["close"] - df["pre_close"]) / df["pre_close"].replace(0, np.nan)) * 100

            # 估算成交额（收盘价 × 成交量，近似值）
            df["amount"] = df["close"] * df["vol"]

            if len(df) == 0:
                return None

            df.sort_values("trade_date", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df

        except Exception as exc:
            logger.warning("akshare 获取 %s 失败: %s", index_code, str(exc)[:100])
            return None

    def get_index_daily(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        获取单个指数日线行情
        数据源: akshare > 模拟数据

        Args:
            index_code: 指数代码，如 "000001.SH"
            start_date: 开始日期 "YYYYMMDD"
            end_date: 结束日期 "YYYYMMDD"

        Returns:
            DataFrame with columns:
              trade_date, open, high, low, close, pre_close,
              pct_chg, vol, amount
        """
        # 优先: akshare（免费）
        df = self._get_from_akshare(index_code, start_date, end_date)
        if df is not None and len(df) > 0:
            logger.info("akshare 获取 %s 成功: %d 条记录", index_code, len(df))
            return df

        # 回退: 模拟数据
        logger.warning("%s 无法获取真实数据，回退到模拟数据", index_code)
        return self._mock_index_daily(index_code, start_date, end_date)

    def get_all_index_daily(
        self,
        start_date: str,
        end_date: str,
    ) -> Dict[str, pd.DataFrame]:
        """
        批量获取4大宽基指数日线行情

        Returns:
            {index_code: DataFrame, ...}
        """
        logger.info("批量获取指数行情: %s ~ %s", start_date, end_date)
        result = {}
        for code, name in INDEX_CODE_MAP.items():
            logger.info("  获取 %s (%s)...", name, code)
            df = self.get_index_daily(code, start_date, end_date)
            result[code] = df
        return result

    def get_actual_pct_change(
        self,
        index_code: str,
        target_date: str,
    ) -> Optional[float]:
        """
        获取指定日期的实际涨跌幅（用于回测）

        Args:
            index_code: 指数代码
            target_date: 目标日期 "YYYYMMDD"

        Returns:
            涨跌幅（百分比），失败返回None
        """
        df = self.get_index_daily(index_code, target_date, target_date)
        if df is not None and len(df) > 0:
            return float(df.iloc[0]["pct_chg"])
        return None

    # ==================== 模拟数据生成 ====================

    @staticmethod
    def _mock_index_daily(
        index_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """生成模拟指数日线数据"""
        logger.info("生成 %s 模拟行情数据 %s~%s", index_code, start_date, end_date)

        start = datetime.strptime(start_date, "%Y%m%d")
        end = datetime.strptime(end_date, "%Y%m%d")

        records = []
        current = start
        # 各指数不同的基准价格
        base_prices = {
            "000001.SH": 3200.0,
            "000300.SH": 3900.0,
            "000905.SH": 6200.0,
            "000852.SH": 6800.0,
        }
        base_price = base_prices.get(index_code, 3500.0)
        price = base_price + np.random.uniform(-200, 200)

        while current <= end:
            # 跳过周末
            if current.weekday() < 5:  # Mon=0, Fri=4
                pct_chg = np.random.normal(0.0002, 0.012)  # 均值微正，标准差1.2%
                pre_close = price
                price = price * (1 + pct_chg)
                open_p = pre_close * (1 + np.random.uniform(-0.003, 0.003))
                high_p = max(open_p, price) * (1 + abs(np.random.uniform(0, 0.01)))
                low_p = min(open_p, price) * (1 - abs(np.random.uniform(0, 0.01)))
                vol = np.random.uniform(100000, 500000)

                records.append({
                    "ts_code": index_code,
                    "trade_date": current.strftime("%Y%m%d"),
                    "open": round(open_p, 2),
                    "high": round(high_p, 2),
                    "low": round(low_p, 2),
                    "close": round(price, 2),
                    "pre_close": round(pre_close, 2),
                    "pct_chg": round(pct_chg * 100, 4),  # 百分比
                    "vol": round(vol, 0),
                    "amount": round(vol * price, 0),
                })
            current += timedelta(days=1)

        df = pd.DataFrame(records)
        df.sort_values("trade_date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df


# ==================== 中国交易日历辅助 ====================

class TradingCalendar:
    """
    交易日历工具
    判断某日是否为A股交易日，获取最近交易日
    """

    @staticmethod
    def is_trading_day(check_date: Optional[date] = None) -> bool:
        """
        判断是否为A股交易日（简化版：工作日=交易日）
        生产环境建议接入Tushare trade_cal接口
        """
        if check_date is None:
            check_date = date.today()
        # 周末不是交易日
        if check_date.weekday() >= 5:
            return False
        return True

    @staticmethod
    def get_latest_trading_day(reference_date: Optional[date] = None) -> date:
        """获取最近的交易日"""
        if reference_date is None:
            reference_date = date.today()
        d = reference_date
        while not TradingCalendar.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    @staticmethod
    def get_next_trading_day(reference_date: Optional[date] = None) -> date:
        """获取下一个交易日"""
        if reference_date is None:
            reference_date = date.today()
        d = reference_date + timedelta(days=1)
        while not TradingCalendar.is_trading_day(d):
            d += timedelta(days=1)
        return d


# ==================== 便捷函数 ====================

def run_index_data_fetch() -> Dict[str, pd.DataFrame]:
    """
    便捷函数: 获取4大指数最新行情
    """
    provider = IndexDataProvider()
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    return provider.get_all_index_daily(start_date, end_date)


# ==================== 本地测试入口 ====================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    provider = IndexDataProvider()
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")

    # 测试单指数
    df = provider.get_index_daily("000001.SH", start, end)
    print(f"\n上证指数行情 ({start}~{end}):")
    if df is not None and len(df) > 0:
        print(df.tail(5).to_string(index=False))

    # 测试交易日
    print(f"\n今日是否为交易日: {TradingCalendar.is_trading_day()}")
    print(f"最近交易日: {TradingCalendar.get_latest_trading_day()}")
    print(f"下一个交易日: {TradingCalendar.get_next_trading_day()}")