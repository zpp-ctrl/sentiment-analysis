# -*- coding: utf-8 -*-
"""
============================================================
视频内容提取模块 - Video Content Extractor
============================================================
功能:
  1. 下载抖音/小红书视频到本地临时目录
  2. 用 ffmpeg 提取音频轨道
  3. 用 OpenAI Whisper 将音频转为文字（语音→文本）
  4. 用 OpenCV 按间隔提取关键帧
  5. 调用 VisionProxy 多模态模型分析画面内容
  6. 将所有提取的文本合并到帖子的 post_content 中

流水线:
  视频URL → 下载视频 → ffmpeg提取音频 → Whisper语音转文字
                      → OpenCV抽帧 → VisionProxy画面分析
                      → 合并文本 → 送入情感分析

容错:
  每个步骤独立容错，任何环节失败不影响其他环节。
  整个模块失败不影响主流水线（回退到纯文本分析）。

使用前提:
  1. 系统安装 ffmpeg: winget install ffmpeg (Windows) / apt install ffmpeg (Linux)
  2. pip install opencv-python openai-whisper Pillow
  3. 首次运行 Whisper 会自动下载模型 (~142MB for base)
  4. Vision 分析需要配置 VISION_OPENAI_API_KEY 或 PaddleOCR
"""

import os
import io
import re
import sys
import json
import time
import shutil
import hashlib
import logging
import tempfile
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ==================== 临时文件管理 ====================

class TempFileManager:
    """
    临时文件上下文管理器
    在 output/video_cache/{date}/ 下创建独立子目录，支持自动清理

    使用:
        with TempFileManager(post_id) as tmp_dir:
            video_path = os.path.join(tmp_dir, "video.mp4")
            # ... 处理 ...
        # 退出时自动清理（除非配置保留）
    """

    def __init__(self, post_id: str):
        from config import VIDEO_CACHE_DIR, VIDEO_KEEP_TEMP_FILES
        self.post_id = post_id
        self.cache_root = VIDEO_CACHE_DIR
        self.keep_files = VIDEO_KEEP_TEMP_FILES
        self.tmp_dir: Optional[str] = None
        self._created_dirs: List[str] = []

    def __enter__(self) -> str:
        today = datetime.now().strftime("%Y%m%d")
        # 使用 post_id 的 hash 避免文件名过长
        safe_id = hashlib.md5(self.post_id.encode()).hexdigest()[:12]
        self.tmp_dir = os.path.join(self.cache_root, today, f"{safe_id}_{self.post_id[:20]}")
        os.makedirs(self.tmp_dir, exist_ok=True)
        self._created_dirs.append(self.tmp_dir)

        # 清理旧缓存（超出大小限制时）
        self._cleanup_old_cache()

        return self.tmp_dir

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.keep_files and self.tmp_dir and os.path.exists(self.tmp_dir):
            try:
                shutil.rmtree(self.tmp_dir, ignore_errors=True)
                logger.debug("已清理临时目录: %s", self.tmp_dir)
            except Exception as e:
                logger.debug("清理临时目录失败: %s", e)

    def _cleanup_old_cache(self):
        """如果缓存目录超出大小限制，清理最旧的文件"""
        from config import VIDEO_MAX_CACHE_SIZE_MB
        try:
            if not os.path.exists(self.cache_root):
                return

            # 计算缓存总大小
            total_size = 0
            file_info = []
            for root, dirs, files in os.walk(self.cache_root):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(fp)
                        total_size += sz
                        file_info.append((os.path.getmtime(fp), sz, fp))
                    except OSError:
                        continue

            max_bytes = VIDEO_MAX_CACHE_SIZE_MB * 1024 * 1024
            if total_size <= max_bytes:
                return

            # 按修改时间排序，删除最旧的文件
            file_info.sort(key=lambda x: x[0])
            deleted = 0
            for _, sz, fp in file_info:
                try:
                    os.remove(fp)
                    total_size -= sz
                    deleted += 1
                    if total_size <= max_bytes * 0.7:  # 清理到70%阈值
                        break
                except OSError:
                    continue

            if deleted > 0:
                logger.info("已清理 %d 个旧缓存文件（释放空间）", deleted)

        except Exception as e:
            logger.debug("缓存清理检查失败: %s", e)


# ==================== 视频下载器 ====================

class VideoDownloader:
    """平台适配的视频下载器"""

    @staticmethod
    def check_ffmpeg() -> bool:
        """检查 ffmpeg 是否可用"""
        return shutil.which("ffmpeg") is not None

    @staticmethod
    def download(
        post_url: str,
        platform: str,
        output_path: str,
        timeout: int = 60,
        max_duration: int = 180,
    ) -> bool:
        """
        下载视频到本地

        Args:
            post_url: 帖子URL
            platform: 平台 (douyin / xiaohongshu)
            output_path: 输出文件路径 (.mp4)
            timeout: 下载超时秒数
            max_duration: 最大下载时长（只下载前N秒）

        Returns:
            True=下载成功, False=失败
        """
        if platform == "douyin":
            return VideoDownloader._download_douyin(post_url, output_path, timeout, max_duration)
        elif platform == "xiaohongshu":
            return VideoDownloader._download_xiaohongshu(post_url, output_path, timeout, max_duration)
        else:
            logger.warning("未知平台: %s，尝试通用下载", platform)
            return VideoDownloader._download_generic(post_url, output_path, timeout, max_duration)

    @staticmethod
    def _download_generic(url: str, output_path: str, timeout: int, max_duration: int) -> bool:
        """通用 HTTP 下载 + ffmpeg 截断"""
        import requests

        try:
            # 先用 requests 下载
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.douyin.com/",
            }

            resp = requests.get(url, headers=headers, stream=True, timeout=timeout)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "video" not in content_type and "octet-stream" not in content_type:
                # 可能不是直接的视频URL，尝试用 ffmpeg 直接下载
                logger.debug("URL 不是直接视频链接（Content-Type: %s），尝试 ffmpeg 下载", content_type)
                return VideoDownloader._download_via_ffmpeg(url, output_path, timeout, max_duration)

            # 流式写入，限制大小
            max_bytes = max_duration * 500 * 1024  # 估算：500KB/s * max_duration
            downloaded = 0
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > max_bytes * 2:  # 2倍冗余
                            break

            if downloaded > 1024:  # 至少下载了 1KB
                # 用 ffmpeg 截断到 max_duration
                return VideoDownloader._trim_video(output_path, max_duration)
            return False

        except Exception as exc:
            logger.warning("通用下载失败: %s", str(exc)[:150])
            # 最后尝试 ffmpeg
            return VideoDownloader._download_via_ffmpeg(url, output_path, timeout, max_duration)

    @staticmethod
    def _download_douyin(url: str, output_path: str, timeout: int, max_duration: int) -> bool:
        """抖音视频下载"""
        # 抖音视频 URL 通常是 https://www.douyin.com/video/{id}
        # 实际的视频流 URL 需要通过页面解析获取
        # 这里使用 ffmpeg + yt-dlp 作为主方案
        return VideoDownloader._download_via_ffmpeg(url, output_path, timeout, max_duration)

    @staticmethod
    def _download_xiaohongshu(url: str, output_path: str, timeout: int, max_duration: int) -> bool:
        """小红书视频下载"""
        return VideoDownloader._download_via_ffmpeg(url, output_path, timeout, max_duration)

    @staticmethod
    def _download_via_ffmpeg(url: str, output_path: str, timeout: int, max_duration: int) -> bool:
        """
        使用 ffmpeg 直接下载和截断
        ffmpeg -t {max_duration} -i {url} -c copy -y {output_path}
        """
        if not VideoDownloader.check_ffmpeg():
            logger.warning("ffmpeg 未安装，无法下载视频")
            return False

        try:
            cmd = [
                "ffmpeg",
                "-t", str(max_duration),          # 只下载前N秒
                "-i", url,                         # 输入URL
                "-c", "copy",                      # 不重新编码（快速）
                "-y",                              # 覆盖已有文件
                "-loglevel", "error",              # 只输出错误
                output_path,
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 30,  # ffmpeg 超时时长稍长
            )

            if result.returncode == 0 and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                if file_size > 1024:
                    logger.debug("ffmpeg 下载成功: %s (%.1f KB)", output_path, file_size / 1024)
                    return True
                else:
                    logger.debug("ffmpeg 下载的文件太小: %d bytes", file_size)
                    return False
            else:
                stderr = result.stderr[:200] if result.stderr else ""
                logger.debug("ffmpeg 下载失败 (code=%d): %s", result.returncode, stderr)
                return False

        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg 下载超时 (%ds)", timeout + 30)
            return False
        except FileNotFoundError:
            logger.warning("ffmpeg 未安装或不在 PATH 中")
            return False
        except Exception as exc:
            logger.warning("ffmpeg 下载异常: %s", str(exc)[:150])
            return False

    @staticmethod
    def _trim_video(video_path: str, max_duration: int) -> bool:
        """用 ffmpeg 截断视频到指定时长"""
        if not VideoDownloader.check_ffmpeg():
            return os.path.exists(video_path) and os.path.getsize(video_path) > 1024

        tmp_path = video_path + ".trimmed.mp4"
        try:
            cmd = [
                "ffmpeg",
                "-t", str(max_duration),
                "-i", video_path,
                "-c", "copy",
                "-y",
                "-loglevel", "error",
                tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and os.path.exists(tmp_path):
                shutil.move(tmp_path, video_path)
                return True
            return os.path.exists(video_path) and os.path.getsize(video_path) > 1024
        except Exception:
            return os.path.exists(video_path) and os.path.getsize(video_path) > 1024


# ==================== 音频提取 & 语音识别 ====================

class AudioExtractor:
    """从视频中提取音频并转录为文字"""

    _whisper_model = None
    _whisper_model_size = None
    _model_lock = threading.Lock()

    @staticmethod
    def extract_audio(video_path: str, audio_path: str) -> bool:
        """
        用 ffmpeg 提取音频轨道

        Args:
            video_path: 输入视频路径
            audio_path: 输出音频路径 (.wav, 16kHz mono)

        Returns:
            True=成功
        """
        if not VideoDownloader.check_ffmpeg():
            logger.warning("ffmpeg 未安装，无法提取音频")
            return False

        try:
            cmd = [
                "ffmpeg",
                "-i", video_path,
                "-vn",                           # 去掉视频流
                "-acodec", "pcm_s16le",           # PCM 16-bit
                "-ar", "16000",                    # 16kHz 采样率
                "-ac", "1",                        # 单声道
                "-y",
                "-loglevel", "error",
                audio_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and os.path.exists(audio_path):
                audio_size = os.path.getsize(audio_path)
                if audio_size > 1024:  # 至少1KB
                    logger.debug("音频提取成功: %.1f KB", audio_size / 1024)
                    return True
            return False
        except Exception as exc:
            logger.warning("音频提取失败: %s", str(exc)[:150])
            return False

    @classmethod
    def transcribe(cls, audio_path: str) -> str:
        """
        用 Whisper 将音频转为文字

        Args:
            audio_path: 16kHz mono WAV 文件路径

        Returns:
            转录文字（失败时返回空字符串）
        """
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1024:
            return ""

        model = cls._load_whisper()
        if model is None:
            return ""

        from config import WHISPER_LANGUAGE

        try:
            result = model.transcribe(
                audio_path,
                language=WHISPER_LANGUAGE,
                fp16=False,               # CPU 推理用 fp32
                verbose=False,
            )
            text = result.get("text", "").strip()
            if text:
                logger.debug("Whisper 转录成功: %d 字", len(text))
            return text
        except Exception as exc:
            logger.warning("Whisper 转录失败: %s", str(exc)[:150])
            return ""

    @classmethod
    def _load_whisper(cls):
        """懒加载 Whisper 模型（线程安全）"""
        from config import WHISPER_MODEL_SIZE

        if cls._whisper_model is not None and cls._whisper_model_size == WHISPER_MODEL_SIZE:
            return cls._whisper_model

        with cls._model_lock:
            if cls._whisper_model is not None and cls._whisper_model_size == WHISPER_MODEL_SIZE:
                return cls._whisper_model

            try:
                import whisper
                logger.info("正在加载 Whisper 模型 (%s)... 首次加载会下载 ~142MB", WHISPER_MODEL_SIZE)
                cls._whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
                cls._whisper_model_size = WHISPER_MODEL_SIZE
                logger.info("Whisper 模型加载完成")
                return cls._whisper_model
            except ImportError:
                logger.warning("openai-whisper 未安装，语音转录不可用。pip install openai-whisper")
                return None
            except Exception as exc:
                # 尝试回退到 tiny 模型
                if WHISPER_MODEL_SIZE != "tiny":
                    logger.warning("Whisper %s 模型加载失败: %s，尝试 tiny", WHISPER_MODEL_SIZE, str(exc)[:100])
                    try:
                        import whisper
                        cls._whisper_model = whisper.load_model("tiny")
                        cls._whisper_model_size = "tiny"
                        logger.info("Whisper tiny 模型加载完成（回退方案）")
                        return cls._whisper_model
                    except Exception:
                        pass
                logger.warning("Whisper 模型加载失败，语音转录不可用")
                return None


# ==================== 帧提取器 ====================

class FrameExtractor:
    """用 OpenCV 从视频中按间隔提取关键帧"""

    @staticmethod
    def extract_key_frames(
        video_path: str,
        interval_seconds: int = 10,
        max_frames: int = 10,
        jpeg_quality: int = 70,
    ) -> List[bytes]:
        """
        从视频中提取关键帧

        Args:
            video_path: 视频文件路径
            interval_seconds: 帧间隔（秒）
            max_frames: 最大帧数
            jpeg_quality: JPEG 压缩质量 (1-100)

        Returns:
            帧图片列表（JPEG bytes），按时间顺序
        """
        if not os.path.exists(video_path):
            logger.warning("视频文件不存在: %s", video_path)
            return []

        try:
            import cv2
        except ImportError:
            logger.warning("opencv-python 未安装，帧提取不可用")
            return []

        cap = None
        frames = []

        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                logger.warning("无法打开视频文件: %s", video_path)
                return []

            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            if fps <= 0:
                fps = 30.0  # 默认30fps

            duration_sec = total_frames / fps if fps > 0 else 0
            logger.debug("视频信息: fps=%.1f, 总帧数=%d, 时长=%.1fs", fps, total_frames, duration_sec)

            # 计算采样帧号
            frame_interval = int(fps * interval_seconds)
            if frame_interval < 1:
                frame_interval = 1

            sample_frames = []
            for i in range(max_frames):
                frame_idx = i * frame_interval
                if frame_idx >= total_frames:
                    break
                sample_frames.append(frame_idx)

            if not sample_frames:
                # 至少取第一帧
                sample_frames = [0]

            logger.debug("计划提取 %d 帧: %s", len(sample_frames), sample_frames[:5])

            for frame_idx in sample_frames:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()

                if not ret:
                    continue

                # 压缩为 JPEG
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
                success, jpeg_bytes = cv2.imencode(".jpg", frame, encode_params)
                if success:
                    frames.append(jpeg_bytes.tobytes())

            logger.debug("帧提取完成: %d/%d 帧", len(frames), len(sample_frames))
            return frames

        except Exception as exc:
            logger.warning("帧提取失败: %s", str(exc)[:150])
            return []
        finally:
            if cap is not None:
                cap.release()


# ==================== 视频内容提取器 ====================

class VideoContentExtractor:
    """
    视频内容提取器 - 主入口

    对 DataFrame 中 post_type='video' 的帖子提取内容并丰富 post_content

    使用:
        extractor = VideoContentExtractor()
        enriched_df = extractor.enrich_dataframe(posts_df)
        # posts_df 中视频帖子的 post_content 已包含语音+画面内容
    """

    def __init__(self):
        from config import (
            VIDEO_FRAME_INTERVAL_SECONDS,
            VIDEO_MAX_FRAMES,
            VIDEO_FRAME_JPEG_QUALITY,
            VIDEO_MAX_DURATION_SECONDS,
            VIDEO_DOWNLOAD_TIMEOUT,
            VIDEO_EXTRACTION_PER_POST_TIMEOUT,
            VIDEO_EXTRACTION_CONCURRENCY,
            VIDEO_ENRICHED_CONTENT_MAX_CHARS,
        )
        self.frame_interval = VIDEO_FRAME_INTERVAL_SECONDS
        self.max_frames = VIDEO_MAX_FRAMES
        self.jpeg_quality = VIDEO_FRAME_JPEG_QUALITY
        self.max_duration = VIDEO_MAX_DURATION_SECONDS
        self.download_timeout = VIDEO_DOWNLOAD_TIMEOUT
        self.per_post_timeout = VIDEO_EXTRACTION_PER_POST_TIMEOUT
        self.concurrency = VIDEO_EXTRACTION_CONCURRENCY
        self.content_max_chars = VIDEO_ENRICHED_CONTENT_MAX_CHARS

        # 延迟初始化 VisionProxy（避免启动时加载模型）
        self._vision_proxy = None

        # 统计
        self._stats = {
            "total_video_posts": 0,
            "downloaded": 0,
            "audio_extracted": 0,
            "frames_extracted": 0,
            "audio_transcribed": 0,
            "vision_analyzed": 0,
            "enriched": 0,
            "failed": 0,
        }

        # 环境检查
        self._ffmpeg_available = VideoDownloader.check_ffmpeg()
        if not self._ffmpeg_available:
            logger.warning("⚠️  ffmpeg 未安装！视频下载和音频提取将不可用。")
            logger.warning("    Windows: winget install ffmpeg")
            logger.warning("    Linux:   sudo apt install ffmpeg")

    def get_stats(self) -> dict:
        """获取提取统计"""
        return dict(self._stats)

    @property
    def _get_vision_proxy(self):
        """延迟初始化 VisionProxy"""
        if self._vision_proxy is None:
            from module_vision_proxy import VisionProxy
            self._vision_proxy = VisionProxy()
        return self._vision_proxy

    # ==================== 主入口 ====================

    def enrich_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        对 DataFrame 中 post_type='video' 的帖子提取视频内容

        Args:
            df: 包含 post_type, post_url, post_content, platform 列的 DataFrame

        Returns:
            修改后的 DataFrame（video 帖子的 post_content 已丰富）
        """
        if df is None or len(df) == 0:
            return df

        df = df.copy()

        # 确保必要列存在
        if "post_type" not in df.columns:
            logger.warning("DataFrame 缺少 post_type 列，跳过视频提取")
            return df

        # 筛选视频帖子
        video_mask = df["post_type"] == "video"
        video_count = video_mask.sum()

        if video_count == 0:
            logger.info("无视频帖子，跳过内容提取")
            return df

        self._stats["total_video_posts"] = video_count
        logger.info("开始处理 %d 条视频帖子 (并发=%d, 超时=%ds/条)...",
                    video_count, self.concurrency, self.per_post_timeout)

        # 准备处理任务
        video_rows = df[video_mask]
        tasks = []

        for idx, row in video_rows.iterrows():
            post_url = str(row.get("post_url", ""))
            platform = str(row.get("platform", "douyin"))
            post_id = str(row.get("post_id", f"unknown_{idx}"))

            if not post_url or post_url in ("nan", "None", ""):
                logger.debug("跳过无URL的视频帖子: %s", post_id)
                continue

            tasks.append((idx, post_url, platform, post_id))

        if not tasks:
            logger.info("所有视频帖子均无有效URL，跳过")
            return df

        # 并发处理
        results = {}  # idx -> enriched_content dict

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {}
            for idx, post_url, platform, post_id in tasks:
                future = executor.submit(
                    self._extract_with_timeout,
                    post_url, platform, post_id, self.per_post_timeout,
                )
                futures[future] = (idx, post_id)

            for future in as_completed(futures):
                idx, post_id = futures[future]
                try:
                    result = future.result()
                    results[idx] = result
                except Exception as exc:
                    logger.warning("视频处理异常 [%s]: %s", post_id, str(exc)[:150])
                    results[idx] = {"audio_text": "", "visual_text": "", "error": str(exc)[:200]}
                    self._stats["failed"] += 1

        # 将提取结果写回 DataFrame
        for idx, extracted in results.items():
            if idx not in df.index:
                continue

            audio_text = extracted.get("audio_text", "")
            visual_text = extracted.get("visual_text", "")
            error = extracted.get("error", "")

            if not error or audio_text or visual_text:
                # 有内容提取成功
                original_content = str(df.at[idx, "post_content"])

                # 构建丰富后的内容
                parts = [original_content]

                if audio_text:
                    parts.append(f"\n\n[视频语音内容]: {audio_text}")

                if visual_text:
                    parts.append(f"\n\n[视频画面内容]: {visual_text}")

                enriched = "".join(parts)

                # 截断到最大长度
                if len(enriched) > self.content_max_chars:
                    enriched = enriched[:self.content_max_chars - 3] + "..."

                df.at[idx, "post_content"] = enriched

                # 写入独立字段（用于数据库存储）
                df.at[idx, "video_content_extracted"] = True
                df.at[idx, "audio_transcript"] = audio_text[:2000] if audio_text else None
                df.at[idx, "visual_description"] = visual_text[:2000] if visual_text else None

                self._stats["enriched"] += 1
            elif error:
                df.at[idx, "video_content_extracted"] = False
                df.at[idx, "audio_transcript"] = None
                df.at[idx, "visual_description"] = None
                self._stats["failed"] += 1

        # 确保所有视频行都有这三个字段
        for col in ["video_content_extracted", "audio_transcript", "visual_description"]:
            if col not in df.columns:
                df[col] = None

        # 未处理的视频行填充默认值
        for idx in df[video_mask].index:
            if pd.isna(df.at[idx, "video_content_extracted"]):
                df.at[idx, "video_content_extracted"] = False
                df.at[idx, "audio_transcript"] = None
                df.at[idx, "visual_description"] = None

        logger.info(
            "视频内容提取完成: 总计=%d, 丰富=%d, 失败=%d, 下载成功=%d, "
            "音频转录=%d, 画面分析=%d",
            self._stats["total_video_posts"],
            self._stats["enriched"],
            self._stats["failed"],
            self._stats["downloaded"],
            self._stats["audio_transcribed"],
            self._stats["vision_analyzed"],
        )

        return df

    def _extract_with_timeout(
        self, post_url: str, platform: str, post_id: str, timeout: int
    ) -> dict:
        """带超时的单条视频提取"""
        result_holder = {"audio_text": "", "visual_text": "", "error": ""}
        exception_holder = {}

        def worker():
            try:
                res = self.extract_from_post(post_url, platform, post_id)
                result_holder.update(res)
            except Exception as exc:
                exception_holder["exc"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            logger.warning("视频处理超时 [%s]: %ds", post_id, timeout)
            self._stats["failed"] += 1
            return {"audio_text": "", "visual_text": "",
                    "error": f"处理超时 ({timeout}s)"}

        if "exc" in exception_holder:
            raise exception_holder["exc"]

        return result_holder

    def extract_from_post(
        self, post_url: str, platform: str, post_id: str
    ) -> Dict[str, str]:
        """
        提取单个视频帖子的内容（完整流水线）

        Args:
            post_url: 帖子URL
            platform: 平台 (douyin / xiaohongshu)
            post_id: 帖子ID（用于日志和文件命名）

        Returns:
            {"audio_text": str, "visual_text": str, "error": str}
            error 为空表示成功
        """
        audio_text = ""
        visual_text = ""
        error = ""

        # 使用临时目录
        with TempFileManager(post_id) as tmp_dir:
            video_path = os.path.join(tmp_dir, f"{post_id[:30]}.mp4")
            audio_path = os.path.join(tmp_dir, f"{post_id[:30]}.wav")

            # ---- 步骤1: 下载视频 ----
            logger.debug("[%s] 开始下载视频...", post_id)
            download_ok = VideoDownloader.download(
                post_url, platform, video_path,
                timeout=self.download_timeout,
                max_duration=self.max_duration,
            )

            if not download_ok or not os.path.exists(video_path):
                error = "视频下载失败"
                logger.info("[%s] %s", post_id, error)
                return {"audio_text": "", "visual_text": "", "error": error}

            self._stats["downloaded"] += 1
            file_size = os.path.getsize(video_path)
            logger.debug("[%s] 视频下载成功: %.1f KB", post_id, file_size / 1024)

            # ---- 步骤2: 提取音频 & 语音转文字 ----
            if self._ffmpeg_available:
                logger.debug("[%s] 提取音频...", post_id)
                audio_ok = AudioExtractor.extract_audio(video_path, audio_path)
                if audio_ok:
                    self._stats["audio_extracted"] += 1
                    logger.debug("[%s] 语音转录...", post_id)
                    audio_text = AudioExtractor.transcribe(audio_path)
                    if audio_text:
                        self._stats["audio_transcribed"] += 1
                        logger.debug("[%s] 语音转录: %d 字", post_id, len(audio_text))

            # ---- 步骤3: 提取关键帧 & 画面分析 ----
            logger.debug("[%s] 提取关键帧...", post_id)
            frames = FrameExtractor.extract_key_frames(
                video_path,
                interval_seconds=self.frame_interval,
                max_frames=self.max_frames,
                jpeg_quality=self.jpeg_quality,
            )

            if frames:
                self._stats["frames_extracted"] += 1
                logger.debug("[%s] 提取了 %d 帧，开始画面分析...", post_id, len(frames))

                proxy = self._get_vision_proxy
                if proxy.is_available:
                    visual_text = proxy.analyze_frames(frames)
                    if visual_text:
                        self._stats["vision_analyzed"] += 1
                        logger.debug("[%s] 画面分析: %d 字", post_id, len(visual_text))
                else:
                    logger.debug("[%s] 无可用视觉后端，跳过画面分析", post_id)
            else:
                logger.debug("[%s] 未能提取到关键帧", post_id)

            # ---- 汇总 ----
            if not audio_text and not visual_text:
                error = "未提取到任何内容（音频和画面分析均无结果）"
                logger.info("[%s] %s", post_id, error)
            else:
                logger.info("[%s] 提取成功: 音频=%d字, 画面=%d字",
                           post_id, len(audio_text), len(visual_text))

        return {"audio_text": audio_text, "visual_text": visual_text, "error": error}


# ==================== 工具函数 ====================

def extract_from_urls(
    urls: List[str],
    platform: str = "douyin",
    concurrency: int = 2,
) -> List[Dict]:
    """
    便捷函数：从 URL 列表批量提取视频内容

    Args:
        urls: 视频 URL 列表
        platform: 平台名称
        concurrency: 并发数

    Returns:
        [{"url": str, "audio_text": str, "visual_text": str, "error": str}, ...]
    """
    extractor = VideoContentExtractor()
    extractor.concurrency = concurrency

    results = []
    for i, url in enumerate(urls):
        post_id = f"batch_{i}"
        res = extractor.extract_from_post(url, platform, post_id)
        res["url"] = url
        res["index"] = i
        results.append(res)
        logger.info("[%d/%d] %s", i + 1, len(urls),
                   "OK" if not res["error"] else f"FAIL: {res['error'][:50]}")

    return results


# ==================== 本地测试入口 ====================

if __name__ == "__main__":
    # Fix Windows GBK encoding for special chars
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(module)s:%(lineno)d | %(message)s",
    )

    print("=" * 60)
    print("  Video Content Extractor - Self Check")
    print("=" * 60)

    # Environment check
    print("\n[Environment]")
    print(f"  ffmpeg: {'[OK] Available' if VideoDownloader.check_ffmpeg() else '[MISSING] Please install'}")
    print(f"  Python: {sys.version}")

    # Whisper check
    try:
        import whisper
        print(f"  Whisper: [OK] Installed")
    except ImportError:
        print(f"  Whisper: [MISSING] pip install openai-whisper")

    # OpenCV check
    try:
        import cv2
        print(f"  OpenCV: [OK] Installed (ver {cv2.__version__})")
    except ImportError:
        print(f"  OpenCV: [MISSING] pip install opencv-python")

    # Vision backend check
    print("\n[Vision Backend]")
    from module_vision_proxy import VisionProxy
    proxy = VisionProxy()
    print(f"  Active backend: {proxy.backend_name}")
    print(f"  Available: {proxy.is_available}")

    # Usage examples
    print("\n" + "=" * 60)
    print("  Usage Examples:")
    print("=" * 60)
    print("""
# 1. Single video extraction
extractor = VideoContentExtractor()
result = extractor.extract_from_post(
    post_url="https://www.douyin.com/video/xxxxx",
    platform="douyin",
    post_id="test_001",
)
print(result)

# 2. DataFrame batch processing
import pandas as pd
df = pd.DataFrame([{
    "post_id": "dy_001",
    "post_type": "video",
    "post_url": "https://www.douyin.com/video/xxxxx",
    "post_content": "Today A-share market surged!",
    "platform": "douyin",
}])
extractor = VideoContentExtractor()
enriched_df = extractor.enrich_dataframe(df)
print(enriched_df["post_content"].iloc[0])
""")
