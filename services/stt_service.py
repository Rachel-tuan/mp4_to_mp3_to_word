"""Speech-to-text provider abstraction.

STT must never be hard-coded to one vendor (spec 五). Everything goes
through the ``STTProvider`` interface:

    transcribe(audio_path) -> Transcript

Only ``OpenAICompatibleSTT`` (a REST call to an OpenAI-style
``/audio/transcriptions`` endpoint, e.g. OpenAI Whisper API or any
compatible gateway) is implemented in v1. ``FasterWhisperSTT`` is stubbed
out for a later local-inference version and intentionally does NOT bundle
any model weights.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("app.stt")


class STTError(RuntimeError):
    """Base class for STT failures."""


class STTNetworkError(STTError):
    """Network-level failure (timeout, connection refused, DNS, ...)."""


class STTAPIError(STTError):
    """The API responded but with an error status."""


@dataclass
class Transcript:
    text: str
    language: Optional[str] = None
    raw: Optional[dict] = None


class STTProvider:
    """Abstract base class for all STT providers."""

    name = "base"

    def transcribe(self, audio_path: str) -> Transcript:
        raise NotImplementedError


class OpenAICompatibleSTT(STTProvider):
    """Calls an OpenAI-compatible ``/v1/audio/transcriptions`` endpoint."""

    name = "openai_compatible"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "whisper-1",
        timeout: int = 120,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.environ.get("STT_API_KEY", "")
        self.base_url = (base_url or os.environ.get("STT_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def transcribe(self, audio_path: str) -> Transcript:
        if not self.api_key:
            raise STTAPIError("未配置 STT API Key（环境变量 STT_API_KEY 或 UI 中输入）")
        if not os.path.isfile(audio_path):
            raise STTError(f"音频文件不存在: {audio_path}")

        import requests  # local import: keep base import light

        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                with open(audio_path, "rb") as f:
                    files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
                    data = {"model": self.model, "response_format": "json"}
                    resp = requests.post(
                        url, headers=headers, files=files, data=data, timeout=self.timeout
                    )
                if resp.status_code >= 500:
                    # Server-side / transient: worth retrying.
                    raise STTNetworkError(f"STT 服务端错误: {resp.status_code}")
                if resp.status_code >= 400:
                    # Client error (bad key, bad file): retrying won't help.
                    raise STTAPIError(f"STT API 错误 {resp.status_code}: {resp.text[:500]}")
                payload = resp.json()
                text = payload.get("text", "")
                return Transcript(text=text, language=payload.get("language"), raw=payload)
            except (STTAPIError,) as e:
                logger.error("STT API error (not retrying): %s", e)
                raise
            except (STTNetworkError, Exception) as e:  # network/timeout/conn errors
                last_exc = e
                wait = min(2 ** attempt, 20)
                logger.warning(
                    "STT attempt %s/%s failed (%s), retrying in %ss",
                    attempt, self.max_retries, e, wait,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)

        raise STTNetworkError(f"STT 请求多次重试后仍然失败: {last_exc}")


class FasterWhisperSTT(STTProvider):
    """Placeholder for a future local faster-whisper backed provider."""

    name = "faster_whisper"

    def __init__(self, model_size: str = "small", **kwargs):
        self.model_size = model_size

    def transcribe(self, audio_path: str) -> Transcript:
        raise NotImplementedError(
            "本地 faster-whisper STT 尚未实现，请使用 openai_compatible provider，"
            "或自行安装 faster-whisper 后扩展本类。"
        )


# Alibaba DashScope provider lives in a separate module (alibaba_stt_service.py)
# to keep this file light and because it has a much larger surface area.
# We register it lazily so importing stt_service never pulls in the DashScope
# upload/polling code unless it is actually requested.

_PROVIDERS = {
    OpenAICompatibleSTT.name: OpenAICompatibleSTT,
    FasterWhisperSTT.name: FasterWhisperSTT,
}


def _ensure_alibaba_registered():
    """Lazily import and register the Alibaba DashScope provider.

    Importing it here keeps the base stt_service module cheap (no extra
    import-time cost for tests that only exercise the OpenAI-compatible
    path), but the moment someone asks for ``alibaba_dashscope`` the full
    implementation becomes available.
    """
    if "alibaba_dashscope" in _PROVIDERS:
        return
    from services.alibaba_stt_service import (
        AlibabaDashScopeSTTProvider,
        DEFAULT_MODEL as ALIBABA_DEFAULT_MODEL,
    )
    _PROVIDERS[AlibabaDashScopeSTTProvider.name] = AlibabaDashScopeSTTProvider


# Default provider used by the UI when nothing is selected explicitly.
DEFAULT_STT_PROVIDER = "alibaba_dashscope"
DEFAULT_STT_MODEL = "qwen-audio-3.1-asr-flash-filetrans"


def get_stt_provider(name: str, **kwargs) -> STTProvider:
    _ensure_alibaba_registered()
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"未知的 STT provider: {name}")
    return cls(**kwargs)
