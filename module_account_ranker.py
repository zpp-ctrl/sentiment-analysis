# -*- coding: utf-8 -*-
"""
============================================================
模块: 跨平台财经自媒体账号排名
============================================================
功能:
  1. 合并抖音 + 小红书账号池
  2. 按粉丝数降序排列，取综合前1000名
  3. 分平台排名 + 综合排名
  4. 导出排名结果供预测模块使用
  5. 每日更新排名快照
============================================================
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional

import pandas as pd

from config import (
    OUTPUT_DIR, RANKED_ACCOUNTS_FILE, RANK_TOP_N,
    PLATFORM_WEIGHTS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)


class AccountRanker:
    """
    跨平台财经账号排名器
    合并抖音和小红书账号池，按粉丝数排序
    """

    def __init__(self):
        self.dy_pool_file = os.path.join(OUTPUT_DIR, "dy_account_pool.json")
        self.xhs_pool_file = os.path.join(OUTPUT_DIR, "xhs_account_pool.json")
        self.dy_rank_file = os.path.join(OUTPUT_DIR, "dy_ranked_top1000.json")
        self.xhs_rank_file = os.path.join(OUTPUT_DIR, "xhs_ranked_top1000.json")

    def load_douyin_accounts(self) -> List[Dict]:
        """加载抖音财经账号"""
        if not os.path.exists(self.dy_pool_file):
            logger.warning("抖音账号池文件不存在: %s", self.dy_pool_file)
            return []

        with open(self.dy_pool_file, "r", encoding="utf-8") as f:
            pool = json.load(f)

        accounts = []
        for name, info in pool.items():
            if not info.get("is_finance_verified") and info.get("finance_score", 0) < 0.4:
                continue  # 跳过非财经账号

            accounts.append({
                "account_name": name,
                "platform": "douyin",
                "user_id": info.get("sec_uid", ""),
                "follower_count": int(info.get("follower_count", 0) or 0),
                "level": info.get("level", ""),
                "finance_score": float(info.get("finance_score", 0) or 0),
                "finance_post_count": int(info.get("finance_post_count", 0) or 0),
                "is_finance_verified": info.get("is_finance_verified", False),
                "last_scraped_date": info.get("last_scraped_date", ""),
                "platform_weight": PLATFORM_WEIGHTS.get("douyin", 1.0),
            })

        logger.info("抖音账号池: %d 个财经账号", len(accounts))
        return accounts

    def load_xiaohongshu_accounts(self) -> List[Dict]:
        """加载小红书财经账号"""
        if not os.path.exists(self.xhs_pool_file):
            logger.warning("小红书账号池文件不存在: %s", self.xhs_pool_file)
            return []

        with open(self.xhs_pool_file, "r", encoding="utf-8") as f:
            pool = json.load(f)

        accounts = []
        for name, info in pool.items():
            if not info.get("is_finance_verified") and info.get("finance_score", 0) < 0.4:
                continue

            accounts.append({
                "account_name": name,
                "platform": "xiaohongshu",
                "user_id": info.get("xhs_user_id", ""),
                "follower_count": int(info.get("follower_count", 0) or 0),
                "level": info.get("level", ""),
                "finance_score": float(info.get("finance_score", 0) or 0),
                "finance_post_count": int(info.get("finance_post_count", 0) or 0),
                "is_finance_verified": info.get("is_finance_verified", False),
                "last_scraped_date": info.get("last_scraped_date", ""),
                "platform_weight": PLATFORM_WEIGHTS.get("xiaohongshu", 0.85),
            })

        logger.info("小红书账号池: %d 个财经账号", len(accounts))
        return accounts

    def merge_and_rank(self, limit: int = RANK_TOP_N) -> Dict:
        """
        合并双平台账号并排名

        Returns:
            {
                "export_time": str,
                "total_accounts": int,        # 总账号数
                "douyin_count": int,           # 抖音账号数
                "xiaohongshu_count": int,      # 小红书账号数
                "top_n": int,                  # 取前N名
                "ranked_accounts": [...],      # 综合排名列表
                "per_platform_top100": {       # 各平台前100
                    "douyin": [...],
                    "xiaohongshu": [...],
                },
                "statistics": {               # 统计信息
                    "total_followers": int,
                    "top1000_min_followers": int,
                    "top1000_max_followers": int,
                    "follower_distribution": {...},  # 粉丝量级分布
                }
            }
        """
        # 加载双平台账号
        dy_accounts = self.load_douyin_accounts()
        xhs_accounts = self.load_xiaohongshu_accounts()

        all_accounts = dy_accounts + xhs_accounts

        if not all_accounts:
            logger.warning("没有可用的财经账号数据")
            return self._empty_result()

        # 按粉丝数严格降序排列
        all_accounts.sort(key=lambda x: x["follower_count"], reverse=True)

        # 综合排名前N
        top_n = all_accounts[:limit]

        # 分配排名
        for rank, acc in enumerate(top_n, 1):
            acc["rank"] = rank

        # 分平台前100
        dy_top = sorted(
            [a for a in all_accounts if a["platform"] == "douyin"],
            key=lambda x: x["follower_count"], reverse=True
        )[:100]
        for rank, acc in enumerate(dy_top, 1):
            acc["platform_rank"] = rank

        xhs_top = sorted(
            [a for a in all_accounts if a["platform"] == "xiaohongshu"],
            key=lambda x: x["follower_count"], reverse=True
        )[:100]
        for rank, acc in enumerate(xhs_top, 1):
            acc["platform_rank"] = rank

        # 粉丝量级分布统计
        distribution = {
            "1000万+": 0, "500万-1000万": 0, "100万-500万": 0,
            "50万-100万": 0, "10万-50万": 0, "10万以下": 0,
        }
        for acc in top_n:
            f = acc["follower_count"]
            if f >= 10_000_000:
                distribution["1000万+"] += 1
            elif f >= 5_000_000:
                distribution["500万-1000万"] += 1
            elif f >= 1_000_000:
                distribution["100万-500万"] += 1
            elif f >= 500_000:
                distribution["50万-100万"] += 1
            elif f >= 100_000:
                distribution["10万-50万"] += 1
            else:
                distribution["10万以下"] += 1

        result = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_accounts": len(all_accounts),
            "douyin_count": len(dy_accounts),
            "xiaohongshu_count": len(xhs_accounts),
            "top_n": limit,
            "ranked_accounts": top_n,
            "per_platform_top100": {
                "douyin": dy_top,
                "xiaohongshu": xhs_top,
            },
            "statistics": {
                "total_followers": sum(a["follower_count"] for a in top_n),
                "top1000_min_followers": top_n[-1]["follower_count"] if top_n else 0,
                "top1000_max_followers": top_n[0]["follower_count"] if top_n else 0,
                "follower_distribution": distribution,
            },
        }

        return result

    def export_ranked_accounts(self, limit: int = RANK_TOP_N) -> str:
        """
        导出综合排名文件
        返回文件路径
        """
        result = self.merge_and_rank(limit)

        if not result["ranked_accounts"]:
            logger.warning("无排名数据可导出")
            return ""

        # 保存综合排名
        os.makedirs(os.path.dirname(RANKED_ACCOUNTS_FILE), exist_ok=True)
        with open(RANKED_ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # 打印摘要
        stats = result["statistics"]
        logger.info("=" * 60)
        logger.info("  跨平台财经自媒体排名 TOP%d", limit)
        logger.info("=" * 60)
        logger.info("  总账号数: %d (抖音:%d, 小红书:%d)",
                   result["total_accounts"], result["douyin_count"],
                   result["xiaohongshu_count"])
        logger.info("  TOP%d 粉丝范围: %d ~ %d",
                   limit, stats["top1000_min_followers"],
                   stats["top1000_max_followers"])
        logger.info("  粉丝量级分布:")
        for tier, count in stats["follower_distribution"].items():
            if count > 0:
                logger.info("    %s: %d个", tier, count)
        logger.info("  排名文件: %s", RANKED_ACCOUNTS_FILE)
        logger.info("=" * 60)

        return RANKED_ACCOUNTS_FILE

    @staticmethod
    def _empty_result() -> Dict:
        return {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_accounts": 0,
            "douyin_count": 0,
            "xiaohongshu_count": 0,
            "top_n": RANK_TOP_N,
            "ranked_accounts": [],
            "per_platform_top100": {"douyin": [], "xiaohongshu": []},
            "statistics": {
                "total_followers": 0,
                "top1000_min_followers": 0,
                "top1000_max_followers": 0,
                "follower_distribution": {},
            },
        }

    def get_top_accounts_for_collection(self, platform: str = None,
                                        limit: int = RANK_TOP_N) -> List[Dict]:
        """
        获取待采集的Top账号列表
        用于指导次日的采集优先级

        Args:
            platform: "douyin" / "xiaohongshu" / None(综合)
            limit: 返回数量
        """
        result = self.merge_and_rank(limit)

        if platform:
            return [a for a in result["ranked_accounts"]
                    if a["platform"] == platform][:limit]
        return result["ranked_accounts"][:limit]


# ==================== 便捷函数 ====================

def run_account_ranking(limit: int = RANK_TOP_N) -> str:
    """
    便捷函数: 执行跨平台账号排名
    返回排名文件路径
    """
    ranker = AccountRanker()
    return ranker.export_ranked_accounts(limit)


def get_top1000_list() -> List[Dict]:
    """获取综合前1000名账号列表"""
    ranker = AccountRanker()
    return ranker.get_top_accounts_for_collection(limit=RANK_TOP_N)


def print_top_accounts(limit: int = 20):
    """打印前N名账号（控制台查看）"""
    ranker = AccountRanker()
    result = ranker.merge_and_rank(limit)

    print(f"\n{'='*80}")
    print(f"  财经自媒体综合排名 TOP{limit}")
    print(f"{'='*80}")
    print(f"{'排名':<6} {'平台':<12} {'账号名':<25} {'粉丝数':<15} {'等级':<12}")
    print("-" * 80)

    for acc in result["ranked_accounts"][:limit]:
        followers_str = _format_followers(acc["follower_count"])
        print(f"{acc['rank']:<6} {acc['platform']:<12} {acc['account_name']:<25} "
              f"{followers_str:<15} {acc['level']:<12}")

    print("-" * 80)
    print(f"统计: 抖音{result['douyin_count']}个 + 小红书{result['xiaohongshu_count']}个")
    print()


def _format_followers(count: int) -> str:
    """格式化粉丝数显示"""
    if count >= 100_000_000:
        return f"{count/100_000_000:.1f}亿"
    elif count >= 10_000:
        return f"{count/10_000:.1f}万"
    else:
        return str(count)


# ==================== 本地测试 ====================
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        print_top_accounts(30)
    else:
        result_path = run_account_ranking()
        if result_path:
            print_top_accounts(20)
