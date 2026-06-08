from __future__ import annotations

from app.services.article_extraction import extract_article_text


def test_extract_article_text_falls_back_to_strip_html():
    html = "<html><body><p>Hello world from fallback.</p></body></html>"
    text = extract_article_text(html)
    assert "Hello world from fallback." in text
