from core.chunking import needs_chunking, split_transcript


def test_short_text_single_chunk():
    text = "这是一段很短的转写文字。"
    assert not needs_chunking(text, max_chars=100)
    chunks = split_transcript(text, max_chars=100)
    assert chunks == [text]


def test_long_text_splits_into_multiple_chunks():
    paragraph = "这是一段测试文字。" * 50  # long
    text = "\n\n".join([paragraph] * 5)
    assert needs_chunking(text, max_chars=500)
    chunks = split_transcript(text, max_chars=500)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 500 or "。" not in c  # hard-cut fallback case allowed
    # reassembling should preserve all non-whitespace content
    assert "".join(chunks).replace("\n", "") .count("这是一段测试文字") == "".join([paragraph]*5).count("这是一段测试文字")


def test_empty_text():
    assert split_transcript("") == []
