# -*- coding: utf-8 -*-
"""
============================================================
LLM 情感分析模块 - 基于 DeepSeek API (OpenAI 兼容)
============================================================
功能:
  1. 调用 DeepSeek API 对帖子文本做财经多空情感分类
  2. 支持单条/批量分析，异步并发 + 失败重试
  3. 输出与 module2 兼容的 sentiment / sentiment_score 字段
  4. 额外输出 confidence / reasoning 字段

使用前提:
  1. pip install openai python-dotenv
  2. 设置环境变量: set DEEPSEEK_API_KEY=sk-xxxxx
  3. 修改 config.py: LLM_ENABLED = True
"""

import os
import re
import json
import logging
import asyncio
import time
from typing import Tuple, List, Dict, Optional

import pandas as pd
from dotenv import load_dotenv

# ★ 自动加载项目根目录的 .env 文件（API Key 写在这个文件里）
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

logger = logging.getLogger(__name__)

# ==================== 系统提示词 ====================

SYSTEM_PROMPT = """你是一位专业的A股财经舆情情感分析专家。你的任务是对财经帖子进行多空情感分类。

## 分析维度
1. **情感极性**: 判断帖子对A股市场/板块/个股表达的是看多(bullish)、看空(bearish)、还是中性(neutral)
2. **财经语境**: 结合利好/利空、政策面、资金面、技术面、基本面等维度综合判断
3. **隐含情绪**: 识别反讽、阴阳怪气、"利好出尽是利空"等隐含表达

## 分类标准
- **bullish（看多）**: 表达对市场/板块的乐观预期、上涨判断、买入建议
- **bearish（看空）**: 表达对市场/板块的悲观预期、下跌判断、卖出建议
- **neutral（中性）**: 客观陈述事实、多空观点均衡、无明确倾向

## 评分规则
- score: 0.0~1.0, >0.55=bullish, <0.45=bearish, 0.45~0.55=neutral
- confidence: 0.0~1.0, 你对判断的确信程度

## 输出格式（严格JSON，按用户要求的格式输出，不要包含任何其他内容）"""


# ==================== Prompt 构建 ====================

def build_user_prompt(text: str) -> str:
    """构造单条分析prompt"""
    text = str(text).strip()
    if len(text) > 1500:
        text = text[:1500] + "..."

    return f"""分析以下财经帖子的多空情感，返回严格的JSON格式（不要包含任何其他内容）：

【帖子内容】
{text}"""


def build_batch_prompt(texts: List[str]) -> str:
    """构造批量分析prompt"""
    items = []
    for i, t in enumerate(texts):
        t = str(t).strip()
        if len(t) > 800:
            t = t[:800] + "..."
        items.append(f"[{i}] {t}")

    numbered_texts = "\n\n".join(items)

    return f"""分析以下 {len(texts)} 条财经帖子的多空情感。对每条帖子返回一个JSON对象，按编号组织。

{numbered_texts}

返回格式（严格的JSON数组，不要包含其他内容）：
[
  {{"id": 0, "sentiment": "bullish", "score": 0.78, "confidence": 0.85, "reasoning": "简短理由"}},
  ...
]"""


# ==================== API 客户端 ====================

def _get_client():
    """获取 OpenAI 兼容客户端（DeepSeek）"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "未设置 DEEPSEEK_API_KEY 环境变量。\n"
            "Windows: set DEEPSEEK_API_KEY=sk-xxxxx\n"
            "Linux/Mac: export DEEPSEEK_API_KEY=sk-xxxxx"
        )

    try:
        import httpx
        from openai import OpenAI
        from config import LLM_BASE_URL
        # ★ trust_env=False 避免 Windows 系统代理干扰（如 Clash/V2Ray 关闭后残留的代理设置）
        http_client = httpx.Client(proxy=None, trust_env=False, timeout=30.0)
        return OpenAI(api_key=api_key, base_url=LLM_BASE_URL, http_client=http_client)
    except ImportError:
        raise ImportError("缺少 openai 库，请执行: pip install openai")


# ==================== API 调用 ====================

def analyze_single_text(
    text: str,
    model: str = "deepseek-chat",
    max_tokens: int = 300,
    temperature: float = 0.0,
    max_retries: int = 3,
) -> Dict:
    """
    调用 DeepSeek API 分析单条文本的情感

    Args:
        text: 待分析的帖子文本
        model: 模型名称
        max_tokens: 最大输出token
        temperature: 温度参数
        max_retries: 失败重试次数

    Returns:
        {"sentiment": "bullish", "score": 0.78, "confidence": 0.85, "reasoning": "..."}
    """
    if not text or not str(text).strip():
        return {"sentiment": "neutral", "score": 0.50, "confidence": 0.50, "reasoning": "空文本"}

    client = _get_client()

    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(str(text))},
                ],
            )

            response_text = response.choices[0].message.content
            return _parse_single_response(response_text)

        except Exception as exc:
            logger.warning("API调用失败 (第%d/%d次): %s", attempt, max_retries, str(exc)[:200])
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 指数退避: 2s, 4s, 8s
            else:
                logger.error("API调用最终失败，返回中性: %s", str(exc)[:200])
                return {"sentiment": "neutral", "score": 0.50, "confidence": 0.0,
                        "reasoning": f"API错误: {str(exc)[:100]}"}


def analyze_batch_texts(
    texts: List[str],
    model: str = "deepseek-chat",
    max_tokens: int = 3000,
    temperature: float = 0.0,
    max_retries: int = 3,
) -> List[Dict]:
    """
    调用 DeepSeek API 批量分析多条文本

    Args:
        texts: 文本列表
        model: 模型名称
        max_tokens: 最大输出token
        temperature: 温度参数
        max_retries: 失败重试次数

    Returns:
        [{"id": 0, "sentiment": "bullish", ...}, ...]
    """
    valid_texts = [str(t).strip() for t in texts if str(t).strip()]
    if not valid_texts:
        return [{"id": i, "sentiment": "neutral", "score": 0.50, "confidence": 0.50,
                 "reasoning": "空文本"} for i in range(len(texts))]

    client = _get_client()

    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_batch_prompt(valid_texts)},
                ],
            )

            response_text = response.choices[0].message.content
            return _parse_batch_response(response_text, len(texts))

        except Exception as exc:
            logger.warning("批量API调用失败 (第%d/%d次): %s", attempt, max_retries, str(exc)[:200])
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            else:
                logger.error("批量API调用最终失败，全部返回中性")
                return [{"id": i, "sentiment": "neutral", "score": 0.50, "confidence": 0.0,
                         "reasoning": f"API错误: {str(exc)[:100]}"} for i in range(len(texts))]


# ==================== 响应解析 ====================

def _extract_json(text: str) -> str:
    """从响应文本中提取JSON部分，优先提取第一个合法JSON值"""
    text = text.strip()

    # 1. 尝试 JSON 代码块
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if json_match:
        return json_match.group(1).strip()

    # 2. 尝试用 raw_decode 定位第一个合法 JSON 值（数组或对象）
    decoder = json.JSONDecoder()
    for start_char in ('[', '{'):
        idx = text.find(start_char)
        if idx >= 0:
            try:
                _, end = decoder.raw_decode(text, idx)
                return text[idx:end].strip()
            except json.JSONDecodeError:
                continue

    return text


def _parse_single_response(response_text: str) -> Dict:
    """解析单条API响应"""
    try:
        json_str = _extract_json(response_text)
        # 先用标准 json.loads，失败则用 raw_decode 容忍尾部内容
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(json_str)
        return {
            "sentiment": _normalize_label(data.get("sentiment", "neutral")),
            "score": _clamp_score(float(data.get("score", 0.50))),
            "confidence": _clamp_score(float(data.get("confidence", 0.50))),
            "reasoning": str(data.get("reasoning", ""))[:200],
        }
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("JSON解析失败: %s | 原始响应: %.200s", str(exc), response_text)
        return {"sentiment": "neutral", "score": 0.50, "confidence": 0.30,
                "reasoning": f"解析失败: {str(exc)[:80]}"}


def _parse_batch_response(response_text: str, expected_count: int) -> List[Dict]:
    """解析批量API响应，截断时尝试恢复部分结果"""
    try:
        json_str = _extract_json(response_text)
        # 先用标准 json.loads，失败则用 raw_decode 容忍尾部内容
        try:
            data_list = json.loads(json_str)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            data_list, _ = decoder.raw_decode(json_str)

        if not isinstance(data_list, list):
            raise ValueError(f"期望JSON数组，实际得到: {type(data_list)}")

        results = []
        for i in range(expected_count):
            # 按id匹配
            matched = None
            for item in data_list:
                if isinstance(item, dict) and item.get("id") == i:
                    matched = item
                    break

            if matched:
                results.append({
                    "id": i,
                    "sentiment": _normalize_label(matched.get("sentiment", "neutral")),
                    "score": _clamp_score(float(matched.get("score", 0.50))),
                    "confidence": _clamp_score(float(matched.get("confidence", 0.50))),
                    "reasoning": str(matched.get("reasoning", ""))[:200],
                })
            else:
                results.append({
                    "id": i, "sentiment": "neutral", "score": 0.50,
                    "confidence": 0.30, "reasoning": "批量结果中未找到此条目"
                })

        return results

    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("批量JSON解析失败: %s | 响应长度=%d | 原始响应前300字: %.300s",
                       str(exc), len(response_text), response_text)

        # ★ 截断恢复: 尝试从原始响应中提取所有完整的 JSON 对象
        partial = _try_extract_partial_objects(response_text, expected_count)
        if partial and any(p["confidence"] > 0 for p in partial):
            logger.info("从截断响应中恢复了 %d/%d 条有效结果",
                        sum(1 for p in partial if p["confidence"] > 0), expected_count)
            return partial

        logger.warning("无法恢复，全部返回中性 (max_tokens 可能不足，需增大)")
        return [{"id": i, "sentiment": "neutral", "score": 0.50, "confidence": 0.0,
                 "reasoning": f"解析失败(响应可能被截断，需增大max_tokens): {str(exc)[:80]}"}
                for i in range(expected_count)]


def _try_extract_partial_objects(text: str, expected_count: int) -> List[Dict]:
    """
    从截断的响应中提取所有完整的 JSON 对象（容错恢复）
    当 API max_tokens 不足导致 JSON 数组被截断时使用
    """
    import re
    results = []
    # 匹配每个完整的 {"id": N, ...} 对象
    pattern = re.compile(r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"sentiment"\s*:\s*"(\w+)"\s*,\s*"score"\s*:\s*([\d.]+)\s*,\s*"confidence"\s*:\s*([\d.]+)\s*,\s*"reasoning"\s*:\s*"([^"]*)"\s*\}')

    for match in pattern.finditer(text):
        try:
            idx = int(match.group(1))
            sentiment = match.group(2)
            score = float(match.group(3))
            confidence = float(match.group(4))
            reasoning = match.group(5)[:200]
            results.append({
                "id": idx,
                "sentiment": _normalize_label(sentiment),
                "score": _clamp_score(score),
                "confidence": _clamp_score(confidence),
                "reasoning": reasoning,
            })
        except (ValueError, IndexError):
            continue

    # 填充缺失项
    for i in range(expected_count):
        if not any(r["id"] == i for r in results):
            results.append({
                "id": i, "sentiment": "neutral", "score": 0.50,
                "confidence": 0.30, "reasoning": "响应截断，未找到此条目"
            })

    # 按 id 排序
    results.sort(key=lambda x: x["id"])
    return results


def _normalize_label(label: str) -> str:
    """标准化情感标签"""
    label = str(label).lower().strip()
    if label in ("bullish", "看多", "positive", "乐观", "利好"):
        return "bullish"
    elif label in ("bearish", "看空", "negative", "悲观", "利空"):
        return "bearish"
    else:
        return "neutral"


def _clamp_score(score: float) -> float:
    """限制得分在 [0, 1] 区间，保留4位小数"""
    return round(max(0.0, min(1.0, score)), 4)


# ==================== 异步并发调用 ====================

async def _analyze_one_async(
    client,
    text: str,
    idx: int,
    model: str,
    max_tokens: int,
    temperature: float,
    semaphore: asyncio.Semaphore,
) -> Tuple[int, Dict]:
    """异步分析单条文本"""
    async with semaphore:
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: analyze_single_text(text, model, max_tokens, temperature, max_retries=1)
            )
            return idx, result
        except Exception as exc:
            logger.error("异步分析异常 [idx=%d]: %s", idx, str(exc)[:150])
            return idx, {"sentiment": "neutral", "score": 0.50, "confidence": 0.0,
                         "reasoning": f"异常: {str(exc)[:100]}"}


async def _analyze_concurrent(
    texts_with_idx: List[Tuple[int, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    concurrency: int,
) -> List[Tuple[int, Dict]]:
    """异步并发分析多条文本"""
    # 注意: 异步阶段复用同步 _get_client，每个协程各自创建自己的 client
    semaphore = asyncio.Semaphore(concurrency)

    tasks = [
        _analyze_one_async(None, text, idx, model, max_tokens, temperature, semaphore)
        for idx, text in texts_with_idx
    ]

    results = await asyncio.gather(*tasks)
    return results


# ==================== 对外主接口 ====================

def classify_with_llm(
    df: pd.DataFrame,
    content_col: str = "post_content",
    model: Optional[str] = None,
    batch_size: Optional[int] = None,
    concurrency: Optional[int] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> pd.DataFrame:
    """
    使用 DeepSeek API 对 DataFrame 中的帖子进行情感分类

    优先使用批量模式（一批多条一起发），剩余不足一批的逐条并发。

    Args:
        df: 包含帖子内容的 DataFrame
        content_col: 文本列名
        model: 模型名，默认用 config.LLM_MODEL
        batch_size: 每批条数，默认用 config.LLM_BATCH_SIZE
        concurrency: 并发数，默认用 config.LLM_CONCURRENCY
        max_tokens: 最大输出token，默认用 config.LLM_MAX_TOKENS
        temperature: 温度，默认用 config.LLM_TEMPERATURE

    Returns:
        添加了 sentiment, sentiment_score, llm_confidence, llm_reasoning 列的 DataFrame
    """
    from config import (
        LLM_MODEL, LLM_MAX_TOKENS, LLM_TEMPERATURE,
        LLM_BATCH_SIZE, LLM_CONCURRENCY,
    )

    model = model or LLM_MODEL
    batch_size = batch_size or LLM_BATCH_SIZE
    concurrency = concurrency or LLM_CONCURRENCY
    max_tokens = max_tokens or LLM_MAX_TOKENS
    temperature = temperature if temperature is not None else LLM_TEMPERATURE

    if df is None or len(df) == 0:
        logger.warning("输入 DataFrame 为空，跳过 LLM 情感分类")
        return df

    total = len(df)
    logger.info("🤖 LLM情感分类开始: 共 %d 条 | 模型=%s | 批大小=%d | 并发=%d",
                total, model, batch_size, concurrency)

    # 准备数据
    texts = []
    for _, row in df.iterrows():
        text = str(row.get(content_col, ""))
        texts.append(text)

    all_results: List[Dict] = [None] * total

    # 阶段1: 批量调用（每批 batch_size 条，一次API调用分析多条）
    batch_count = total // batch_size
    if batch_count > 0:
        logger.info("阶段1: 批量分析 %d 批 (每批%d条)...", batch_count, batch_size)
        for b in range(batch_count):
            start = b * batch_size
            end = start + batch_size
            batch_texts = texts[start:end]

            logger.info("  批次 %d/%d: [%d ~ %d]", b + 1, batch_count, start, end - 1)
            # ★ 批量调用需要足够大的 max_tokens 避免 JSON 响应被截断
            # 每条帖子约需 150~200 tokens（JSON 结构 + 中文 reasoning）
            # 至少 3000 tokens，20条一批需要约 4000 tokens
            batch_max_tokens = max(batch_size * 200, 3000)
            batch_results = analyze_batch_texts(
                batch_texts, model=model, max_tokens=batch_max_tokens,
                temperature=temperature
            )

            for item in batch_results:
                idx = start + item["id"]
                if idx < total:
                    all_results[idx] = {
                        "sentiment": item["sentiment"],
                        "score": item["score"],
                        "confidence": item["confidence"],
                        "reasoning": item["reasoning"],
                    }

    # 阶段2: 剩余文本异步并发逐条分析
    remaining_start = batch_count * batch_size
    remaining_texts = texts[remaining_start:]

    if remaining_texts:
        logger.info("阶段2: 并发分析剩余 %d 条...", len(remaining_texts))
        texts_with_idx = [(remaining_start + i, t) for i, t in enumerate(remaining_texts)]

        loop = asyncio.new_event_loop()
        try:
            async_results = loop.run_until_complete(
                _analyze_concurrent(texts_with_idx, model, max_tokens, temperature, concurrency)
            )
            for idx, result in async_results:
                all_results[idx] = {
                    "sentiment": result["sentiment"],
                    "score": result["score"],
                    "confidence": result["confidence"],
                    "reasoning": result["reasoning"],
                }
        finally:
            loop.close()

    # 填充缺失值
    for i in range(total):
        if all_results[i] is None:
            all_results[i] = {"sentiment": "neutral", "score": 0.50, "confidence": 0.0,
                              "reasoning": "未分析"}

    # 写入 DataFrame
    df = df.copy()
    df["sentiment"] = [r["sentiment"] for r in all_results]
    df["sentiment_score"] = [r["score"] for r in all_results]
    df["llm_confidence"] = [r["confidence"] for r in all_results]
    df["llm_reasoning"] = [r["reasoning"] for r in all_results]

    # 统计分布
    dist = df["sentiment"].value_counts().to_dict()
    avg_conf = df["llm_confidence"].mean()
    logger.info(
        "🤖 LLM情感分类完成: bullish=%d, bearish=%d, neutral=%d | 平均置信度=%.2f",
        dist.get("bullish", 0), dist.get("bearish", 0), dist.get("neutral", 0), avg_conf,
    )

    return df


# ==================== 本地测试入口 ====================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    # 检查 API Key
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("=" * 60)
        print("⚠️  未设置 DEEPSEEK_API_KEY 环境变量")
        print("请先设置: set DEEPSEEK_API_KEY=sk-xxxxx")
        print("=" * 60)
    else:
        # 测试单条分析
        test_texts = [
            "今天A股暴涨，上证指数放量突破3300点，北向资金大幅流入，牛市来了！",
            "市场暴跌，恐慌情绪蔓延，建议减仓避险，技术面已破位。",
            "今天行情不温不火，多空分歧较大，维持震荡格局，继续观望。",
            "政策利好不断，降准降息预期升温，但量能不足反弹空间有限。",
        ]

        print("\n" + "=" * 60)
        print("  测试1: 单条分析")
        print("=" * 60)
        for t in test_texts:
            result = analyze_single_text(t)
            print(f"\n文本: {t[:50]}...")
            print(f"情感: {result['sentiment']} | 得分: {result['score']} | "
                  f"置信度: {result['confidence']}")
            print(f"理由: {result['reasoning'][:100]}")

        # 测试批量
        print("\n" + "=" * 60)
        print("  测试2: 批量分析 (2条一批)")
        print("=" * 60)
        batch_results = analyze_batch_texts(test_texts[:2])
        for r in batch_results:
            print(f"  [{r['id']}] {r['sentiment']} | score={r['score']} | conf={r['confidence']}")

        # 测试 DataFrame 接口
        print("\n" + "=" * 60)
        print("  测试3: DataFrame 接口")
        print("=" * 60)
        test_df = pd.DataFrame({
            "post_content": test_texts,
            "platform": ["douyin"] * 4,
        })
        result_df = classify_with_llm(test_df)
        print(result_df[["sentiment", "sentiment_score", "llm_confidence"]].to_string())
