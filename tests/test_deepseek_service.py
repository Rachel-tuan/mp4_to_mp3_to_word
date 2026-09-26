"""Covers spec 十五 item 10: DeepSeek API network failure -> retried, then raised."""
from unittest import mock

import pytest
import requests

from services.deepseek_service import DeepSeekAPIError, DeepSeekNetworkError, DeepSeekService


def test_missing_api_key_raises():
    svc = DeepSeekService(api_key="")
    with pytest.raises(DeepSeekAPIError):
        svc.generate_manuscript("一些转写文字")


def test_network_failure_retries_then_raises():
    svc = DeepSeekService(api_key="fake-key", max_retries=2)
    with mock.patch("requests.post", side_effect=requests.exceptions.ConnectionError("boom")), \
         mock.patch("time.sleep", return_value=None):
        with pytest.raises(DeepSeekNetworkError):
            svc.generate_manuscript("一些转写文字")


def test_short_transcript_single_call_pair():
    svc = DeepSeekService(api_key="fake-key")
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {"choices": [{"message": {"content": "# 标题\n\n整理后的正文"}}]}

    with mock.patch("requests.post", return_value=fake_resp) as post_mock:
        result = svc.generate_manuscript("一段简短的转写文字")
    assert result.chunk_count == 1
    assert "标题" in result.markdown
    # one call to clean the chunk, one to synthesize
    assert post_mock.call_count == 2
