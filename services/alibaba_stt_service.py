"""Alibaba Cloud DashScope (Bailian) file-transcription STT provider.

Implements the real DashScope asynchronous file-transcription API for the
``qwen-audio-3.1-asr-flash-filetrans`` model.

Flow (all requests go through plain ``requests`` -- no heavy SDK dep):

  1. GET  /api/v1/uploads?action=getPolicy&model=<model>   -> OSS upload credentials
  2. POST  <upload_host>  (multipart/form-data)              -> put local mp3 on temp OSS
  3. POST  /api/v1/services/audio/asr/transcription         -> submit async task
  4. GET   /api/v1/tasks/<task_id>  (poll loop)              -> wait for SUCCEEDED / FAILED
  5. GET   <transcription_url from task result>              -> download final transcript JSON

Error taxonomy:
  * STTUploadError       -- local file upload to temp OSS failed
  * STTTaskCreationError-- submit returned a non-2xx / no task_id
  * STTTaskFailedError  -- server marked the task FAILED
  * STTTimeoutError     -- polling exceeded the configured deadline
  * STTAuthError        -- 401 / 403 (bad DASHSCOPE_API_KEY)
  * STTRateLimitError   -- 429
  * STTNetworkError     -- connection / timeout at the HTTP level
  * STTAPIError         -- any other 4xx / 5xx
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional

from services.stt_service import (
    STTAPIError,
    STTError,
    STTNetworkError,
    STTProvider,
    Transcript,
)

logger = logging.getLogger("app.stt.alibaba")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_MODEL = "qwen-audio-3.1-asr-flash-filetrans"
DEFAULT_POLL_INTERVAL = 5
DEFAULT_POLL_TIMEOUT = 30 * 60
DEFAULT_HTTP_TIMEOUT = 120


# -- Granular STT error subclasses -----------------------------------------

class STTUploadError(STTError):
    """Local mp3 upload to DashScope temp OSS failed."""


class STTTaskCreationError(STTError):
    """Submit-transcription-task returned a non-success response."""


class STTTaskFailedError(STTError):
    """The DashScope task itself finished with status FAILED."""


class STTTimeoutError(STTError):
    """Polling exceeded ``poll_timeout`` before reaching a terminal state."""


class STTAuthError(STTAPIError):
    """401 / 403 -- bad or missing DASHSCOPE_API_KEY."""


class STTRateLimitError(STTAPIError):
    """429 -- API rate limit hit."""


# -- Helpers ----------------------------------------------------------------

def _classify_http_error(status_code: int, body: str) -> STTAPIError:
    """Map a DashScope HTTP error status to the right STTAPIError subclass."""
    snippet = body[:500] if isinstance(body, str) else str(body)[:500]
    if status_code in (401, 403):
        return STTAuthError(f"DashScope 鉴权失败 (HTTP {status_code}): {snippet}")
    if status_code == 429:
        return STTRateLimitError(f"DashScope 限流 (HTTP 429): {snippet}")
    return STTAPIError(f"DashScope API 错误 (HTTP {status_code}): {snippet}")


# -- Main provider ----------------------------------------------------------

class AlibabaDashScopeSTTProvider(STTProvider):
    """Real DashScope file-transcription provider."""

    name = "alibaba_dashscope"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        poll_timeout: int = DEFAULT_POLL_TIMEOUT,
        http_timeout: int = DEFAULT_HTTP_TIMEOUT,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = (
            base_url
            or os.environ.get("DASHSCOPE_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.environ.get("DASHSCOPE_STT_MODEL") or DEFAULT_MODEL
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.http_timeout = http_timeout
        self.max_retries = max_retries

    # -- public interface -----------------------------------------------

    def transcribe(self, audio_path: str) -> Transcript:
        if not self.api_key:
            raise STTAuthError(
                "未配置 DashScope API Key（环境变量 DASHSCOPE_API_KEY 或 UI 中输入）"
            )
        if not os.path.isfile(audio_path):
            raise STTError(f"音频文件不存在: {audio_path}")

        logger.info(
            "DashScope STT: uploading %s (%d bytes) for model=%s",
            audio_path, os.path.getsize(audio_path), self.model,
        )

        oss_url = self._upload_local_file(audio_path)
        task_id = self._submit_task(oss_url)
        transcript_text = self._poll_until_done(task_id)

        logger.info("DashScope STT: transcript ready (%d chars)", len(transcript_text))
        return Transcript(text=transcript_text, language="zh", raw={"model": self.model})

    # -- step 1+2: get upload policy, then PUT local file to OSS ---------

    def _get_upload_policy(self) -> Dict:
        """Step 1: ask DashScope for OSS upload credentials."""
        import requests

        url = f"{self.base_url}/uploads"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        params = {"action": "getPolicy", "model": self.model}

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(
                    url, headers=headers, params=params, timeout=self.http_timeout
                )
            except requests.exceptions.RequestException as e:
                last_exc = STTNetworkError(f"获取上传凭证网络错误: {e}")
                wait = min(2 ** attempt, 20)
                logger.warning("Upload-policy attempt %s/%s failed: %s, retry in %ss",
                               attempt, self.max_retries, e, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue

            if resp.status_code == 200:
                data = resp.json().get("data")
                if not data:
                    raise STTUploadError(f"上传凭证响应缺少 data 字段: {resp.text[:500]}")
                return data
            err = _classify_http_error(resp.status_code, resp.text)
            if isinstance(err, STTNetworkError) or resp.status_code >= 500:
                last_exc = err
                wait = min(2 ** attempt, 20)
                logger.warning("Upload-policy HTTP %s, retry in %ss", resp.status_code, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue
            raise err

        raise last_exc or STTUploadError("获取上传凭证多次重试后仍然失败")

    def _upload_local_file(self, audio_path: str) -> str:
        """Steps 1+2: get policy, multipart-PUT local file, return oss:// URL."""
        policy = self._get_upload_policy()

        import requests

        upload_host = policy["upload_host"]
        upload_dir = policy["upload_dir"]
        file_name = os.path.basename(audio_path)
        key = f"{upload_dir}/{file_name}"

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with open(audio_path, "rb") as f:
                    files = {
                        "OSSAccessKeyId": (None, policy["oss_access_key_id"]),
                        "Signature": (None, policy["signature"]),
                        "policy": (None, policy["policy"]),
                        "x-oss-object-acl": (None, policy["x_oss_object_acl"]),
                        "x-oss-forbid-overwrite": (None, policy["x_oss_forbid_overwrite"]),
                        "key": (None, key),
                        "success_action_status": (None, "200"),
                        "file": (file_name, f, "audio/mpeg"),
                    }
                    resp = requests.post(upload_host, files=files, timeout=self.http_timeout)
            except requests.exceptions.RequestException as e:
                last_exc = STTUploadError(f"上传文件网络错误: {e}")
                wait = min(2 ** attempt, 20)
                logger.warning("OSS upload attempt %s/%s failed: %s, retry in %ss",
                               attempt, self.max_retries, e, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue

            if resp.status_code == 200:
                oss_url = f"oss://{key}"
                logger.info("Uploaded to temp OSS: %s", oss_url)
                return oss_url
            if resp.status_code >= 500:
                last_exc = STTUploadError(f"OSS 上传失败 (HTTP {resp.status_code}): {resp.text[:300]}")
                wait = min(2 ** attempt, 20)
                logger.warning("OSS upload HTTP %s, retry in %ss", resp.status_code, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue
            raise STTUploadError(f"OSS 上传失败 (HTTP {resp.status_code}): {resp.text[:300]}")

        raise last_exc or STTUploadError("上传文件多次重试后仍然失败")

    # -- step 3: submit async transcription task -------------------------

    def _submit_task(self, oss_url: str) -> str:
        """Step 3: POST /services/audio/asr/transcription, return task_id."""
        import requests

        url = f"{self.base_url}/services/audio/asr/transcription"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
            "X-DashScope-OssResourceResolve": "enable",
        }
        payload = {
            "model": self.model,
            "input": {"file_url": oss_url},
            "parameters": {"channel_id": [0], "enable_itn": False},
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.http_timeout
                )
            except requests.exceptions.RequestException as e:
                last_exc = STTTaskCreationError(f"提交转写任务网络错误: {e}")
                wait = min(2 ** attempt, 20)
                logger.warning("Task-submit attempt %s/%s failed: %s, retry in %ss",
                               attempt, self.max_retries, e, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue

            if resp.status_code == 200:
                data = resp.json()
                task_id = (data.get("output") or {}).get("task_id")
                if not task_id:
                    raise STTTaskCreationError(
                        f"任务提交成功但未返回 task_id: {resp.text[:500]}"
                    )
                logger.info("DashScope STT task submitted: task_id=%s", task_id)
                return task_id

            err = _classify_http_error(resp.status_code, resp.text)
            if resp.status_code >= 500 or isinstance(err, STTRateLimitError):
                last_exc = err
                wait = min(2 ** attempt, 20)
                logger.warning("Task-submit HTTP %s, retry in %ss", resp.status_code, wait)
                if attempt < self.max_retries:
                    time.sleep(wait)
                continue
            raise err

        raise last_exc or STTTaskCreationError("提交转写任务多次重试后仍然失败")

    # -- step 4: poll task status ---------------------------------------

    def _poll_until_done(self, task_id: str) -> str:
        """Step 4: GET /tasks/<task_id> until SUCCEEDED / FAILED / timeout."""
        import requests

        url = f"{self.base_url}/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        deadline = time.time() + self.poll_timeout
        attempt = 0

        while time.time() < deadline:
            attempt += 1
            try:
                resp = requests.get(url, headers=headers, timeout=self.http_timeout)
            except requests.exceptions.RequestException as e:
                logger.warning("Poll attempt %s network error: %s", attempt, e)
                time.sleep(self.poll_interval)
                continue

            if resp.status_code in (401, 403):
                raise STTAuthError(
                    f"查询任务鉴权失败 (HTTP {resp.status_code}): {resp.text[:300]}"
                )
            if resp.status_code == 429:
                logger.warning("Poll hit 429 rate limit, waiting longer")
                time.sleep(self.poll_interval * 3)
                continue
            if resp.status_code >= 500:
                logger.warning("Poll got HTTP %s, retrying", resp.status_code)
                time.sleep(self.poll_interval)
                continue
            if resp.status_code != 200:
                raise _classify_http_error(resp.status_code, resp.text)

            data = resp.json()
            output = data.get("output") or {}
            status = (output.get("task_status") or "").upper()

            logger.info("DashScope STT task %s status=%s", task_id, status)

            if status == "SUCCEEDED":
                return self._download_transcript_text(output)
            if status == "FAILED":
                message = output.get("message") or output.get("code") or "未知错误"
                raise STTTaskFailedError(f"DashScope 转写任务失败: {message}")
            time.sleep(self.poll_interval)

        raise STTTimeoutError(
            f"转写任务超时（超过 {self.poll_timeout} 秒未完成）: task_id={task_id}"
        )

    # -- step 5: download and extract plain text from result -------------

    def _download_transcript_text(self, output: dict) -> str:
        """Given a SUCCEEDED task output dict, download the result and
        concatenate all recognition text segments into one string."""
        import requests

        results = output.get("results") or []
        if results and isinstance(results, list):
            first = results[0]
            if isinstance(first, dict) and first.get("text"):
                return self._flatten_inline_text(first)

        for result in results:
            url = (result or {}).get("transcription_url")
            if not url:
                continue
            try:
                resp = requests.get(url, timeout=self.http_timeout)
            except requests.exceptions.RequestException as e:
                raise STTNetworkError(f"下载转写结果失败: {e}") from e
            if resp.status_code != 200:
                raise STTAPIError(
                    f"下载转写结果失败 (HTTP {resp.status_code}): {resp.text[:300]}"
                )
            try:
                payload = resp.json()
            except ValueError as e:
                raise STTAPIError(f"转写结果不是合法 JSON: {e}") from e
            return self._extract_text_from_result_payload(payload)

        text = output.get("text") or output.get("sentences")
        if isinstance(text, str):
            return text
        if isinstance(text, list):
            return self._flatten_inline_text(output)

        raise STTTaskFailedError(
            f"任务成功但未找到转写结果字段。output keys: {list(output.keys())}"
        )

    @staticmethod
    def _flatten_inline_text(node: dict) -> str:
        """Pull all text strings out of a DashScope result dict."""
        parts = []
        if isinstance(node.get("text"), str):
            parts.append(node["text"])
        for s in node.get("sentences") or []:
            if isinstance(s, dict) and s.get("text"):
                parts.append(s["text"])
        for t in node.get("transcripts") or []:
            if isinstance(t, dict) and t.get("text"):
                parts.append(t["text"])
        return "\n".join(p.strip() for p in parts if p and p.strip())

    @staticmethod
    def _extract_text_from_result_payload(payload: dict) -> str:
        """The downloaded transcription_url JSON can nest a few shapes."""
        if "transcripts" in payload or "sentences" in payload:
            return AlibabaDashScopeSTTProvider._flatten_inline_text(payload)
        result = payload.get("result")
        if isinstance(result, dict):
            return AlibabaDashScopeSTTProvider._flatten_inline_text(result)
        if isinstance(payload.get("text"), str):
            return payload["text"]
        import json
        logger.warning("Unknown transcription result shape, dumping raw payload")
        return json.dumps(payload, ensure_ascii=False)


# -- Registration -----------------------------------------------------------

_PROVIDERS = {
    AlibabaDashScopeSTTProvider.name: AlibabaDashScopeSTTProvider,
}


def register_alibaba_provider(name: str, cls: type):
    """Allow other modules to add providers without editing this file directly."""
    _PROVIDERS[name] = cls


def get_alibaba_stt_provider(name: str, **kwargs) -> STTProvider:
    """Factory used by ui/main_window.py."""
    if name in _PROVIDERS:
        return _PROVIDERS[name](**kwargs)
    from services.stt_service import OpenAICompatibleSTT
    if name == OpenAICompatibleSTT.name:
        return OpenAICompatibleSTT(**kwargs)
    raise ValueError(f"未知的 STT provider: {name}")
