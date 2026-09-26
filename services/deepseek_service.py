"""DeepSeek LLM service.

Calls DeepSeek's OpenAI-compatible chat completions endpoint to turn a raw
transcript into a cleaned-up Markdown manuscript (spec 六 / 七 / 八).

API key handling (spec 六 / 十四):
  * never hard-coded
  * read from DEEPSEEK_API_KEY env var by default
  * UI may pass one in explicitly (not persisted to git / state file)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import List, Optional

from core.chunking import DEFAULT_MAX_CHARS, needs_chunking, split_transcript

logger = logging.getLogger("app.deepseek")

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


class DeepSeekError(RuntimeError):
    pass


class DeepSeekNetworkError(DeepSeekError):
    """Timeouts / connection failures -- safe to retry."""


class DeepSeekAPIError(DeepSeekError):
    """4xx/5xx with a real API response body -- retry only on 5xx."""


def _load_prompt(filename: str) -> str:
    path = os.path.join(_PROMPTS_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@dataclass
class ManuscriptResult:
    markdown: str
    chunk_count: int


class DeepSeekService:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 120,
        max_retries: int = 3,
        max_chunk_chars: int = DEFAULT_MAX_CHARS,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_chunk_chars = max_chunk_chars
        self._chunk_prompt = _load_prompt("manuscript.md")
        self._synthesis_prompt = _load_prompt("synthesis.md")

    # -- low level -----------------------------------------------------
    def _chat(self, prompt: str) -> str:
        if not self.api_key:
            raise DeepSeekAPIError("未配置 DeepSeek API Key（环境变量 DEEPSEEK_API_KEY 或 UI 中输入）")

        import requests  # local import keeps module import cheap/testable

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
            except requests.exceptions.RequestException as e:
                last_exc = DeepSeekNetworkError(f"网络请求失败: {e}")
                wait = min(2 ** attempt, 20)
                logger.warning("DeepSeek attempt %s/%s network error: %s, retrying in %ss",
                               attempt, self.max_retries, e, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue

            if resp.status_code >= 500:
                last_exc = DeepSeekAPIError(f"DeepSeek 服务端错误 {resp.status_code}")
                wait = min(2 ** attempt, 20)
                logger.warning("DeepSeek attempt %s/%s server error %s, retrying in %ss",
                               attempt, self.max_retries, resp.status_code, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue

            if resp.status_code >= 400:
                # Bad key / bad request: retrying is pointless.
                raise DeepSeekAPIError(f"DeepSeek API 错误 {resp.status_code}: {resp.text[:500]}")

            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise DeepSeekAPIError(f"DeepSeek 返回格式异常: {data}") from e

        raise last_exc or DeepSeekNetworkError("DeepSeek 请求多次重试后仍然失败")

    # -- high level ------------------------------------------------------
    def generate_manuscript(self, transcript_text: str, progress_cb=None) -> ManuscriptResult:
        """Transcript -> cleaned Markdown manuscript.

        For short transcripts this is a single call. For long transcripts
        (spec 八) we chunk, clean each chunk, then run one final synthesis
        pass that adds a title/summary/subheadings across the whole thing.
        """
        transcript_text = (transcript_text or "").strip()
        if not transcript_text:
            raise DeepSeekError("转写稿为空，无法生成文稿")

        if not needs_chunking(transcript_text, self.max_chunk_chars):
            cleaned = self._chat(self._chunk_prompt.format(chunk_text=transcript_text))
            final = self._chat(self._synthesis_prompt.format(cleaned_text=cleaned))
            return ManuscriptResult(markdown=final.strip(), chunk_count=1)

        chunks: List[str] = split_transcript(transcript_text, self.max_chunk_chars)
        cleaned_parts: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            if progress_cb:
                progress_cb(i, len(chunks))
            logger.info("DeepSeek cleaning chunk %s/%s (%s chars)", i, len(chunks), len(chunk))
            cleaned_parts.append(self._chat(self._chunk_prompt.format(chunk_text=chunk)))

        combined = "\n\n".join(part.strip() for part in cleaned_parts)
        final = self._chat(self._synthesis_prompt.format(cleaned_text=combined))
        return ManuscriptResult(markdown=final.strip(), chunk_count=len(chunks))
