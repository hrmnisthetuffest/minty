"""208 documents and 662 slide outline to PowerPoint, written without python-pptx."""
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

import pytest

from minty_core.create.documents import Outline, from_outline_text, save, to_html, to_markdown, to_pptx

TEXT = """Climate change
  What is it
    Long-term shift in temperatures
    Mostly caused by burning fossil fuels
  Evidence
    Rising sea levels
    Shrinking glaciers
    - Warmer oceans
  What we can do
    Walk, cycle or take the bus
    Save energy at home
"""


def test_an_indented_outline_becomes_a_title_and_slides_with_bullets():
    o = from_outline_text(TEXT)
    assert o.title == "Climate change" and [s.title for s in o.slides] == ["What is it", "Evidence", "What we can do"]
    assert o.slides[1].bullets == ["Rising sea levels", "Shrinking glaciers", "Warmer oceans"]


def test_limits_keep_a_deck_sane():
    many = "Deck\n" + "\n".join(f"  Slide {i}\n    point" for i in range(100))
    assert len(from_outline_text(many).slides) <= 60
    long = "Deck\n  Slide\n" + "\n".join(f"    point {i}" for i in range(30))
    assert len(from_outline_text(long).slides[0].bullets) <= 10
    with pytest.raises(ValueError):
        from_outline_text("   ")


def test_markdown_and_html_carry_everything_and_the_html_is_inert():
    o = from_outline_text(TEXT + "  <script>alert(1)</script>\n    <img onerror=x>\n")
    md = to_markdown(o)
    assert md.startswith("# Climate change") and "## Evidence" in md and "- Rising sea levels" in md
    page = to_html(o)
    assert "<script" not in page.lower().replace("&lt;script&gt;", "") and "&lt;img onerror=x&gt;" in page
    assert "Content-Security-Policy" in page and "default-src 'none'" in page


def parts(data: bytes) -> dict[str, bytes]:
    z = zipfile.ZipFile(BytesIO(data))
    assert z.testzip() is None
    return {n: z.read(n) for n in z.namelist()}


def test_the_pptx_is_a_well_formed_package_with_one_slide_per_slide_plus_a_title_slide():
    o = from_outline_text(TEXT)
    p = parts(to_pptx(o))
    for name, raw in p.items():
        ET.fromstring(raw)  # every part is well-formed XML
    assert "[Content_Types].xml" in p and "_rels/.rels" in p and "ppt/presentation.xml" in p
    slides = sorted(n for n in p if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
    assert len(slides) == 4  # title + three
    first = p[slides[0]].decode()
    assert "Climate change" in first
    assert "Rising sea levels" in p[slides[2]].decode() and "Walk, cycle or take the bus" in p[slides[3]].decode()


def test_every_relationship_points_at_a_part_that_exists_and_nothing_points_outside_the_file():
    p = parts(to_pptx(from_outline_text(TEXT)))
    import posixpath

    for name, raw in p.items():
        if not name.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(name))  # the folder the rels file describes
        for rel in ET.fromstring(raw):
            assert rel.attrib.get("TargetMode") != "External"
            target = posixpath.normpath(posixpath.join(base, rel.attrib["Target"]))
            assert target in p, (name, rel.attrib["Target"])


def test_every_part_has_a_content_type():
    p = parts(to_pptx(from_outline_text(TEXT)))
    types = ET.fromstring(p["[Content_Types].xml"])
    ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    overrides = {e.attrib["PartName"].lstrip("/") for e in types.findall(f"{ns}Override")}
    defaults = {e.attrib["Extension"] for e in types.findall(f"{ns}Default")}
    for name in p:
        assert name in overrides or name.rsplit(".", 1)[-1] in defaults, name
    for name in overrides:
        assert name in p, name


def test_slide_text_is_escaped_so_markup_in_a_title_cannot_break_or_inject():
    o = from_outline_text("Deck\n  A <b>bold</b> & \"quoted\" title\n    x < y and a & b\n    \x07bell char\n")
    p = parts(to_pptx(o))
    slide = next(raw for n, raw in p.items() if n == "ppt/slides/slide2.xml").decode()
    ET.fromstring(slide)
    assert "&lt;b&gt;bold&lt;/b&gt;" in slide and "x &lt; y and a &amp; b" in slide and "\x07" not in slide


def test_saving_never_overwrites_and_is_owner_only(tmp_path):
    a = save(tmp_path / "out", "Climate change", b"one", "pptx")
    b = save(tmp_path / "out", "Climate change", b"two", "pptx")
    assert a.name == "climate-change.pptx" and b.name == "climate-change-1.pptx"
    assert a.read_bytes() == b"one" and b.read_bytes() == b"two"
    assert oct(a.stat().st_mode & 0o777) == "0o600"
    c = save(tmp_path / "out", "../../etc/passwd", b"x", "md")
    assert c.parent == tmp_path / "out" and ".." not in c.name
    with pytest.raises(ValueError):
        save(tmp_path / "out", "x", b"x", "exe")
