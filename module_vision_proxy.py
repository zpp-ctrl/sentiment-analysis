# -*- coding: utf-8 -*-
"""
============================================================
多模态视觉模型代理层 - Vision Proxy
============================================================
功能:
  1. 抽象视觉模型后端（策略模式）：GPT-4o / Qwen-VL / PaddleOCR
  2. 自动按优先级回退：主后端不可用时切换到备用后端
  3. 对视频关键帧进行画面分析，提取文字/图表/情绪信息

设计理念:
  DeepSeek API 目前不支持原生图片输入，因此视觉分析由独立后端完成。
  提取的文本描述再送入 DeepSeek 做情感分析（参见 llm_sentiment.py）。

后端对比:
  | 后端             | 能力                    | 成本      | 使用场景        |
  | OpenAI Vision   | 完整画面理解(文字+图表+情绪) | API费用  | 追求质量        |
  | Qwen-VL         | 本地部署，中文理解好      | 显卡资源  | 内网/高隐私     |
  | PaddleOCR       | 仅提取画面中的文字        | 免费      | 轻量兜底        |

使用前提:
  - OpenAI Vision: 设置 VISION_OPENAI_API_KEY 环境变量
  - Qwen-VL: 本地启动 vLLM/transformers HTTP 服务
  - PaddleOCR: pip install paddlepaddle paddleocr
"""

import os
import io
import re
import json
import base64
import logging
import time
from abc import ABC, abstractmethod
from typing import List, Optional

logger = logging.getLogger(__name__)


# ==================== 视觉分析 Prompt ====================

VISION_SYSTEM_PROMPT = """你是一位专业的A股财经视频画面分析专家。你的任务是从视频关键帧中提取与金融市场相关的信息。

## 分析维度
1. **屏幕文字**: 提取画面中所有可见的中文文字、数字、股票代码、指数数值
2. **图表信息**: 描述K线图、分时图、技术指标等图表的形态和关键数据
3. **人物情绪**: 如果画面中有博主/主播，描述其表情和语气暗示的情绪（乐观/担忧/激动/平静）
4. **财经信息**: 画面中展示的任何与A股、板块、个股、政策相关的信息

## 输出要求
- 用中文描述，简洁但全面
- 如果有具体的数字/代码/指标，务必保留原始数值
- 如果画面中没有财经相关信息，简单回复"无财经相关信息"
- 回复控制在150字以内"""


def build_vision_user_prompt(frame_index: int, total_frames: int) -> str:
    """构造单帧分析 prompt"""
    return f"请分析这段财经视频的第 {frame_index + 1}/{total_frames} 个关键帧画面，提取其中展示的财经信息。"


# ==================== 抽象基类 ====================

class VisionBackend(ABC):
    """视觉模型后端抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名称标识"""
        ...

    @abstractmethod
    def analyze_frame(self, image_bytes: bytes, frame_index: int, total_frames: int) -> str:
        """
        分析单帧画面

        Args:
            image_bytes: JPEG 格式的图片字节数据
            frame_index: 帧序号（从0开始）
            total_frames: 总帧数

        Returns:
            画面描述文本
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """快速检查后端是否可用"""
        ...

    def _image_to_base64(self, image_bytes: bytes) -> str:
        """将图片字节转为 base64 data URL"""
        return base64.b64encode(image_bytes).decode("utf-8")


# ==================== OpenAI Vision 后端 ====================

class OpenAIVisionBackend(VisionBackend):
    """
    使用 OpenAI 兼容的 Vision API（GPT-4o / GPT-4o-mini 等）
    也兼容任何实现了 OpenAI Vision 接口的服务（阿里百炼、智谱GLM-4V等）
    """

    @property
    def name(self) -> str:
        return "openai_vision"

    def __init__(self):
        from config import (
            VISION_OPENAI_API_KEY,
            VISION_OPENAI_BASE_URL,
            VISION_OPENAI_MODEL,
            VISION_OPENAI_MAX_TOKENS,
            VISION_OPENAI_TIMEOUT,
        )
        self.api_key = VISION_OPENAI_API_KEY
        self.base_url = VISION_OPENAI_BASE_URL
        self.model = VISION_OPENAI_MODEL
        self.max_tokens = VISION_OPENAI_MAX_TOKENS
        self.timeout = VISION_OPENAI_TIMEOUT
        self._client = None

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            from openai import OpenAI
            return True
        except ImportError:
            logger.warning("openai 库未安装，OpenAI Vision 后端不可用")
            return False

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def analyze_frame(self, image_bytes: bytes, frame_index: int, total_frames: int) -> str:
        image_b64 = self._image_to_base64(image_bytes)
        data_url = f"data:image/jpeg;base64,{image_b64}"

        client = self._get_client()
        user_text = build_vision_user_prompt(frame_index, total_frames)

        try:
            response = client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": VISION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": data_url, "detail": "low"}},
                        ],
                    },
                ],
            )
            result = response.choices[0].message.content.strip()
            logger.debug("[OpenAI Vision] 帧 %d/%d 分析完成: %.80s",
                         frame_index + 1, total_frames, result)
            return result

        except Exception as exc:
            logger.warning("[OpenAI Vision] 帧 %d 分析失败: %s", frame_index + 1, str(exc)[:150])
            raise


# ==================== Qwen-VL 本地后端 ====================

class QwenVLBackend(VisionBackend):
    """
    使用本地部署的 Qwen-VL 模型（通过 OpenAI 兼容接口）
    需要先启动 vLLM 或 transformers HTTP 服务
    """

    @property
    def name(self) -> str:
        return "qwen_vl"

    def __init__(self):
        from config import VISION_QWEN_API_URL, VISION_QWEN_MODEL_NAME
        self.api_url = VISION_QWEN_API_URL
        self.model_name = VISION_QWEN_MODEL_NAME
        self._available = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import requests
            resp = requests.get(f"{self.api_url}/models", timeout=5)
            self._available = resp.status_code == 200
        except Exception:
            self._available = False
            logger.debug("[Qwen-VL] 本地服务不可达: %s", self.api_url)
        return self._available

    def analyze_frame(self, image_bytes: bytes, frame_index: int, total_frames: int) -> str:
        import requests

        image_b64 = self._image_to_base64(image_bytes)
        data_url = f"data:image/jpeg;base64,{image_b64}"
        user_text = build_vision_user_prompt(frame_index, total_frames)

        payload = {
            "model": self.model_name,
            "max_tokens": 200,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }

        try:
            resp = requests.post(
                f"{self.api_url}/chat/completions",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()["choices"][0]["message"]["content"].strip()
            logger.debug("[Qwen-VL] 帧 %d/%d 分析完成: %.80s",
                         frame_index + 1, total_frames, result)
            return result
        except Exception as exc:
            logger.warning("[Qwen-VL] 帧 %d 分析失败: %s", frame_index + 1, str(exc)[:150])
            raise


# ==================== OCR 文字提取后端（EasyOCR） ====================

class OCRVisionBackend(VisionBackend):
    """
    使用 EasyOCR 提取画面中的文字（免费、本地运行，基于 PyTorch）
    不做画面理解和场景分析，只提取可见文字

    优势: 安装简单(pip install easyocr)，跨平台兼容性好，中文识别准确率高
    """

    @property
    def name(self) -> str:
        return "ocr"

    def __init__(self):
        from config import VISION_OCR_LANG
        self.lang = VISION_OCR_LANG
        self._reader = None
        self._init_error = None

    def is_available(self) -> bool:
        if self._reader is not None:
            return True
        if self._init_error is not None:
            return False
        try:
            import easyocr
            # EasyOCR uses language codes like ['ch_sim', 'en'] for Chinese + English
            lang_list = ['ch_sim', 'en'] if self.lang == 'ch' else [self.lang]
            self._reader = easyocr.Reader(lang_list, gpu=False, verbose=False)
            logger.info("[EasyOCR] 初始化成功, lang=%s", lang_list)
            return True
        except ImportError:
            self._init_error = "easyocr 未安装，请执行: pip install easyocr"
            logger.warning("[EasyOCR] %s", self._init_error)
            return False
        except Exception as exc:
            self._init_error = str(exc)
            logger.warning("[EasyOCR] 初始化失败: %s", str(exc)[:150])
            return False

    def analyze_frame(self, image_bytes: bytes, frame_index: int, total_frames: int) -> str:
        if not self.is_available():
            raise RuntimeError(self._init_error or "EasyOCR 不可用")

        import numpy as np
        from PIL import Image

        # Convert bytes to numpy array
        image = Image.open(io.BytesIO(image_bytes))
        img_array = np.array(image)

        try:
            # EasyOCR returns list of (bbox, text, confidence) tuples
            results = self._reader.readtext(img_array)
            texts = []
            for (bbox, text, confidence) in results:
                t = str(text).strip()
                if t and confidence > 0.3:  # Filter low-confidence results
                    texts.append(t)

            combined = " ".join(texts) if texts else ""
            logger.debug("[EasyOCR] 帧 %d/%d 识别到 %d 条文字: %.80s",
                         frame_index + 1, total_frames, len(texts), combined)
            return combined

        except Exception as exc:
            logger.warning("[EasyOCR] 帧 %d OCR 失败: %s", frame_index + 1, str(exc)[:150])
            raise


# Backward compatibility alias
PaddleOCRBackend = OCRVisionBackend


# ==================== VisionProxy 工厂 ====================

class VisionProxy:
    """
    视觉模型代理 - 自动选择可用的后端

    使用方式:
        proxy = VisionProxy()  # 按 config.VISION_BACKEND_ORDER 尝试
        texts = proxy.analyze_frames([frame_bytes_0, frame_bytes_1, ...])
        # texts 是合并后的描述文本
    """

    # 后端注册表
    BACKEND_REGISTRY = {
        "openai": OpenAIVisionBackend,
        "qwen_vl": QwenVLBackend,
        "ocr": OCRVisionBackend,        # EasyOCR (free, local)
        "paddle_ocr": OCRVisionBackend, # Backward compatibility alias
    }

    def __init__(self, backend_order: Optional[List[str]] = None):
        """
        Args:
            backend_order: 后端优先级列表，如 ["openai", "paddle_ocr"]
                           默认从 config.VISION_BACKEND_ORDER 读取
        """
        if backend_order is None:
            from config import VISION_BACKEND_ORDER
            backend_order = list(VISION_BACKEND_ORDER)

        self._backend_order = backend_order
        self._active_backend: Optional[VisionBackend] = None
        self._active_backend_name: str = ""
        self._init_backend()

    def _init_backend(self):
        """按优先级尝试初始化后端"""
        for name in self._backend_order:
            if name not in self.BACKEND_REGISTRY:
                logger.warning("未知的视觉后端: %s，跳过", name)
                continue

            try:
                backend_cls = self.BACKEND_REGISTRY[name]
                backend = backend_cls()
                if backend.is_available():
                    self._active_backend = backend
                    self._active_backend_name = name
                    logger.info("VisionProxy: 使用 %s 作为视觉分析后端", name)
                    return
                else:
                    logger.info("VisionProxy: %s 后端不可用，尝试下一个", name)
            except Exception as exc:
                logger.warning("VisionProxy: %s 后端初始化异常: %s", name, str(exc)[:150])

        # 所有后端都不可用
        logger.warning("VisionProxy: 所有视觉后端均不可用，画面分析将被跳过")
        self._active_backend = None
        self._active_backend_name = "none"

    @property
    def backend_name(self) -> str:
        return self._active_backend_name

    @property
    def is_available(self) -> bool:
        return self._active_backend is not None

    def analyze_frames(self, frame_images: List[bytes]) -> str:
        """
        分析所有关键帧，返回合并的文本描述

        Args:
            frame_images: 按时间顺序排列的帧图片（JPEG bytes）

        Returns:
            合并的描述文本，如:
            "[画面@0s]: 上证指数3300点，K线呈上升趋势\n[画面@10s]: 博主表情激动，北向资金流入..."
            如果所有帧都无有效内容，返回空字符串
        """
        if not self._active_backend:
            logger.warning("VisionProxy: 无可用后端，跳过画面分析")
            return ""

        if not frame_images:
            return ""

        total = len(frame_images)
        descriptions = []

        for i, img_bytes in enumerate(frame_images):
            try:
                desc = self._active_backend.analyze_frame(img_bytes, i, total)
                if desc and desc.strip():
                    # 标注时间戳
                    from config import VIDEO_FRAME_INTERVAL_SECONDS
                    timestamp_sec = i * VIDEO_FRAME_INTERVAL_SECONDS
                    if timestamp_sec >= 60:
                        ts = f"{timestamp_sec // 60}m{timestamp_sec % 60}s"
                    else:
                        ts = f"{timestamp_sec}s"
                    descriptions.append(f"[画面@{ts}] {desc.strip()}")
            except Exception as exc:
                logger.warning("VisionProxy: 帧 %d 分析失败: %s", i, str(exc)[:100])
                # 单帧失败不影响其他帧
                continue

        return "\n".join(descriptions)


# ==================== 本地测试入口 ====================

if __name__ == "__main__":
    import sys
    # Fix Windows GBK encoding for emoji/special chars
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    print("=" * 60)
    print("  VisionProxy - Self Check")
    print("=" * 60)

    proxy = VisionProxy()
    print(f"\nActive backend: {proxy.backend_name}")
    print(f"Available: {proxy.is_available}")

    if proxy.is_available:
        # Create a test image with Chinese financial text for OCR
        from PIL import Image, ImageDraw
        test_img = Image.new("RGB", (640, 480), color=(30, 30, 30))
        draw = ImageDraw.Draw(test_img)
        draw.text((50, 200), "Shanghai Composite 3300.50 +1.25%", fill=(255, 255, 255))
        draw.text((50, 250), "North-bound inflow 5B CNY", fill=(0, 255, 0))

        buf = io.BytesIO()
        test_img.save(buf, format="JPEG", quality=70)
        test_bytes = buf.getvalue()

        print("\nTesting frame analysis...")
        result = proxy.analyze_frames([test_bytes])
        print(f"Result:\n{result}")
    else:
        print("\n[WARN] No vision backend available. Configure one of:")
        print("  1. Set VISION_OPENAI_API_KEY env var (OpenAI Vision)")
        print("  2. Start local Qwen-VL service (http://127.0.0.1:8000)")
        print("  3. pip install easyocr (free local OCR)")
