from datetime import datetime

from minty_core.web.showpage import open_page, render_page, write_page


def test_everything_is_escaped_and_there_is_no_script():
    page = render_page("<b>Results</b>", [{"title": "<script>alert(1)</script>", "text": "a & b <img onerror=x>",
                                           "url": "https://ok.example/a?x=1&y=2"}])
    assert "<script" not in page.lower() and "onerror=x>" not in page and "&lt;script&gt;" in page
    assert 'href="https://ok.example/a?x=1&amp;y=2"' in page and 'rel="noopener noreferrer"' in page


def test_only_web_links_become_links_and_a_csp_blocks_everything_else():
    page = render_page("t", [{"title": "a", "text": "", "url": "javascript:alert(1)"},
                             {"title": "b", "text": "", "url": "file:///etc/passwd"}])
    assert "javascript:" not in page and "file:" not in page
    assert "Content-Security-Policy" in page and "default-src 'none'" in page and "<a " not in page


def test_the_page_is_written_owner_only_and_opened_by_the_caller(tmp_path):
    path = write_page(tmp_path / "show", "Cheap earbuds", [{"title": "Boat", "text": "₹1,499", "url": ""}],
                      datetime(2026, 10, 18, 9, 30, 1))
    assert path.name == "show-20261018-093001.html" and oct(path.stat().st_mode & 0o777) == "0o600"
    assert "Cheap earbuds" in path.read_text(encoding="utf-8")
    opened = []
    assert open_page(path, lambda cmd: opened.append(cmd) or 0)
    assert opened == [["xdg-open", str(path)]]
