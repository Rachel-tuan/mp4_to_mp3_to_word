import os

from services.document_service import markdown_to_docx, save_text


def test_save_text_writes_file(tmp_path):
    path = str(tmp_path / "sub" / "file.md")
    save_text(path, "# hello")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        assert f.read() == "# hello"


def test_save_text_no_overwrite(tmp_path):
    path = tmp_path / "file.md"
    path.write_text("original")
    save_text(str(path), "new content", overwrite=False)
    assert path.read_text() == "original"


def test_markdown_to_docx(tmp_path):
    docx_path = str(tmp_path / "out.docx")
    md = "# 标题\n\n**摘要**：这是摘要\n\n## 小标题\n\n正文内容"
    markdown_to_docx(md, docx_path)
    assert os.path.isfile(docx_path)

    import docx
    doc = docx.Document(docx_path)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "标题" in text
    assert "小标题" in text
    assert "正文内容" in text
