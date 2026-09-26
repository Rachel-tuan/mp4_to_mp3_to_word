"""Tests for the Alibaba DashScope STT provider.

All tests mock the HTTP layer (requests.*) so they run without any real
network access or real DASHSCOPE_API_KEY.
"""
import json
from unittest import mock

import pytest

from services.alibaba_stt_service import (
    AlibabaDashScopeSTTProvider,
    STTAuthError,
    STTTaskFailedError,
    STTTimeoutError,
    STTUploadError,
)
from services.stt_service import Transcript


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "audio.mp3"
    p.write_bytes(b"fake mp3 bytes")
    return str(p)


@pytest.fixture
def provider():
    return AlibabaDashScopeSTTProvider(
        api_key="fake-dashscope-key",
        poll_interval=0.01,
        poll_timeout=5,
        max_retries=2,
    )


# -- auth / file checks ---------------------------------------------------

def test_missing_api_key_raises_auth_error(audio_file):
    p = AlibabaDashScopeSTTProvider(api_key="")
    with pytest.raises(STTAuthError):
        p.transcribe(audio_file)


def test_missing_audio_file_raises(audio_file):
    p = AlibabaDashScopeSTTProvider(api_key="fake")
    with pytest.raises(Exception, match="音频文件不存在"):
        p.transcribe(audio_file.replace("audio.mp3", "does_not_exist.mp3"))


# -- happy path: full submit -> poll -> download flow --------------------

def _fake_policy_response():
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "upload_host": "https://dashscope-file-test.oss-cn-beijing.aliyuncs.com",
            "upload_dir": "dashscope-instant/test/2024-01-01",
            "oss_access_key_id": "LTAIxxx",
            "signature": "sig=xxx",
            "policy": "base64policy",
            "x_oss_object_acl": "private",
            "x_oss_forbid_overwrite": "true",
        }
    }
    return resp


def _fake_submit_response():
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "output": {"task_id": "task-12345"}
    }
    return resp


def _fake_poll_succeeded():
    """A poll response where the task is already SUCCEEDED."""
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "output": {
            "task_status": "SUCCEEDED",
            "results": [
                {"transcription_url": "https://result.example.com/transcript.json"}
            ],
        }
    }
    return resp


def _fake_download_response():
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "transcripts": [{"text": "你好世界，这是转写结果。"}]
    }
    return resp


def test_full_happy_path(audio_file, provider):
    with mock.patch("requests.get", side_effect=[_fake_policy_response(), _fake_poll_succeeded()]), \
         mock.patch("requests.post", side_effect=[
            mock.Mock(status_code=200),   # OSS upload
            _fake_submit_response(),       # submit task
         ]), \
         mock.patch("requests.get", side_effect=[
            _fake_policy_response(),
            _fake_poll_succeeded(),
            _fake_download_response(),
         ]) as get_mock:

        result = provider.transcribe(audio_file)

    assert isinstance(result, Transcript)
    assert "你好世界" in result.text


# -- error cases -----------------------------------------------------------

def test_auth_error_on_get_policy(audio_file, provider):
    resp = mock.Mock(status_code=401, text="Unauthorized")
    with mock.patch("requests.get", return_value=resp):
        with pytest.raises(STTAuthError):
            provider.transcribe(audio_file)


def test_upload_failure_raises(audio_file, provider):
    upload_resp = mock.Mock(status_code=403, text="Forbidden")
    with mock.patch("requests.get", return_value=_fake_policy_response()), \
         mock.patch("requests.post", return_value=upload_resp):
        with pytest.raises(STTUploadError):
            provider.transcribe(audio_file)


def test_task_failure_raises(audio_file, provider):
    poll_resp = mock.Mock()
    poll_resp.status_code = 200
    poll_resp.json.return_value = {
        "output": {"task_status": "FAILED", "message": "Audio decode error"}
    }
    with mock.patch("requests.get", side_effect=[_fake_policy_response(), poll_resp]), \
         mock.patch("requests.post", side_effect=[
            mock.Mock(status_code=200),
            _fake_submit_response(),
         ]):
        with pytest.raises(STTTaskFailedError, match="Audio decode error"):
            provider.transcribe(audio_file)


def test_timeout_raises(audio_file, provider):
    """Poll keeps returning PENDING until we hit the timeout."""
    pending_resp = mock.Mock()
    pending_resp.status_code = 200
    pending_resp.json.return_value = {
        "output": {"task_status": "PENDING"}
    }

    def fake_get(url, **kwargs):
        # First call: get upload policy. Subsequent calls: poll task (always PENDING).
        if "/uploads" in url:
            return _fake_policy_response()
        return pending_resp

    with mock.patch("requests.get", side_effect=fake_get), \
         mock.patch("requests.post", side_effect=[
            mock.Mock(status_code=200),
            _fake_submit_response(),
         ]), \
         mock.patch("time.sleep", return_value=None):
        with pytest.raises(STTTimeoutError):
            provider.transcribe(audio_file)


def test_transcription_result_inline_text(audio_file, provider):
    """When SUCCEEDED output already contains inline text (no transcription_url)."""
    poll_resp = mock.Mock()
    poll_resp.status_code = 200
    poll_resp.json.return_value = {
        "output": {
            "task_status": "SUCCEEDED",
            "results": [{"text": "直接返回的转写文本"}],
        }
    }
    with mock.patch("requests.get", side_effect=[_fake_policy_response(), poll_resp]), \
         mock.patch("requests.post", side_effect=[
            mock.Mock(status_code=200),
            _fake_submit_response(),
         ]):
        result = provider.transcribe(audio_file)
    assert result.text == "直接返回的转写文本"
