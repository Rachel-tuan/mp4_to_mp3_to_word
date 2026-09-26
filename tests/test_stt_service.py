"""Covers spec 十五 item 9: STT API network failure -> retried, then raised."""
from unittest import mock

import pytest

from services.stt_service import OpenAICompatibleSTT, STTAPIError, STTNetworkError, Transcript


@pytest.fixture
def audio_file(tmp_path):
    p = tmp_path / "audio.mp3"
    p.write_bytes(b"fake mp3 bytes")
    return str(p)


def test_missing_api_key_raises(audio_file):
    provider = OpenAICompatibleSTT(api_key="")
    with pytest.raises(STTAPIError):
        provider.transcribe(audio_file)


def test_network_failure_retries_then_raises(audio_file):
    provider = OpenAICompatibleSTT(api_key="fake-key", max_retries=3)

    with mock.patch("requests.post", side_effect=ConnectionError("boom")), \
         mock.patch("time.sleep", return_value=None):
        with pytest.raises(STTNetworkError):
            provider.transcribe(audio_file)


def test_success_returns_transcript(audio_file):
    provider = OpenAICompatibleSTT(api_key="fake-key")
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {"text": "你好世界", "language": "zh"}

    with mock.patch("requests.post", return_value=fake_resp):
        result = provider.transcribe(audio_file)
    assert isinstance(result, Transcript)
    assert result.text == "你好世界"


def test_client_error_not_retried(audio_file):
    provider = OpenAICompatibleSTT(api_key="bad-key", max_retries=3)
    fake_resp = mock.Mock(status_code=401, text="unauthorized")

    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        return fake_resp

    with mock.patch("requests.post", side_effect=fake_post):
        with pytest.raises(STTAPIError):
            provider.transcribe(audio_file)
    assert call_count["n"] == 1  # no retry on 4xx
