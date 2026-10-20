"""Documents (208) and slides (662): an outline becomes Markdown, an inert HTML page or a PowerPoint file. python-pptx is not
installed, so the .pptx is written here as the standard zip of XML parts (title, text boxes, bullets; no pictures, no links).
Its structure is tested; **opening it in PowerPoint or LibreOffice was not possible here**. Files are never overwritten."""
from __future__ import annotations

import html
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from minty_core.create.mindmap import parse_outline

MAX_SLIDES, MAX_BULLETS = 60, 10
EXTENSIONS = {"pptx", "md", "html", "svg", "txt", "mmd", "png", "jpg", "webp"}
_XML_BAD = re.compile(r"[^\x09\x0a\x0d\x20-퟿-�]")


@dataclass
class Slide:
    title: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class Outline:
    title: str
    slides: list[Slide] = field(default_factory=list)


def from_outline_text(text: str) -> Outline:
    root = parse_outline(text)
    slides = []
    for child in root.children[:MAX_SLIDES]:
        bullets = []
        for n in list(child.walk())[1:]:
            bullets.append(("– " * max(0, n.depth - 2)) + n.label)
        slides.append(Slide(child.label, bullets[:MAX_BULLETS]))
    return Outline(root.label, slides)


def to_markdown(o: Outline) -> str:
    lines = [f"# {o.title}", ""]
    for s in o.slides:
        lines += [f"## {s.title}", "", *[f"- {b}" for b in s.bullets], ""]
    return "\n".join(lines)


def to_html(o: Outline) -> str:
    esc = lambda t: html.escape(t, quote=True)  # noqa: E731
    body = "".join(f"<section><h2>{esc(s.title)}</h2><ul>" + "".join(f"<li>{esc(b)}</li>" for b in s.bullets)
                   + "</ul></section>" for s in o.slides)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(o.title)}</title><style>body{{font:17px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;"
            "padding:0 1rem}section{border-top:1px solid #ccc;padding-top:.6rem}</style></head>"
            f"<body><h1>{esc(o.title)}</h1>{body}</body></html>")


# ---- the .pptx ----------------------------------------------------------------------------------------------------
NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
CT = "application/vnd.openxmlformats-officedocument."
GROUP = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
         '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def _x(text: str) -> str:
    return html.escape(_XML_BAD.sub("", text), quote=False)


def _box(ident: int, name: str, x: int, y: int, w: int, h: int, paras: str, anchor: str = "t") -> str:
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{ident}" name="{name}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm>'
            f'<a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            f'<p:txBody><a:bodyPr wrap="square" anchor="{anchor}"><a:normAutofit/></a:bodyPr><a:lstStyle/>{paras}</p:txBody></p:sp>')


def _para(text: str, size: int, bold: bool = False, bullet: bool = False, centre: bool = False) -> str:
    ppr = ('<a:pPr marL="342900" indent="-342900"><a:buChar char="•"/></a:pPr>' if bullet else
           f'<a:pPr algn="{"ctr" if centre else "l"}"><a:buNone/></a:pPr>')
    weight = ' b="1"' if bold else ""
    return f'<a:p>{ppr}<a:r><a:rPr lang="en-US" sz="{size}"{weight}/><a:t>{_x(text)}</a:t></a:r></a:p>'


def _slide(shapes: str) -> str:
    return (f'{HEAD}<p:sld {NS}><p:cSld><p:spTree>{GROUP}{shapes}</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def _theme() -> str:
    clr = "".join(f'<a:{n}><a:srgbClr val="{v}"/></a:{n}>' for n, v in (
        ("dk1", "1B1B1B"), ("lt1", "FFFFFF"), ("dk2", "44546A"), ("lt2", "E7E6E6"), ("accent1", "0B57D0"),
        ("accent2", "ED7D31"), ("accent3", "A5A5A5"), ("accent4", "FFC000"), ("accent5", "5B9BD5"), ("accent6", "70AD47"),
        ("hlink", "0563C1"), ("folHlink", "954F72")))
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    lines = "".join(f'<a:ln w="{w}">{fill}</a:ln>' for w in (6350, 12700, 19050))
    return (f'{HEAD}<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Minty"><a:themeElements>'
            f'<a:clrScheme name="Minty">{clr}</a:clrScheme><a:fontScheme name="Minty"><a:majorFont><a:latin typeface="Calibri"/>'
            '<a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/>'
            f'<a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="Minty"><a:fillStyleLst>{fill * 3}</a:fillStyleLst>'
            f'<a:lnStyleLst>{lines}'
            '</a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/>'
            f'</a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst>{fill * 3}'
            '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')


def _rels(items: list[tuple[str, str, str]]) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{REL}/{t}" Target="{target}"/>' for i, t, target in items)
    return f'{HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{body}</Relationships>'


def to_pptx(o: Outline) -> bytes:
    slides = [_slide(_box(2, "Title", 914400, 2057400, 10363200, 1371600, _para(o.title, 4800, True, centre=True), "ctr")
                     + _box(3, "Subtitle", 914400, 3600000, 10363200, 700000, _para("Made with Minty", 2000, centre=True)))]
    for s in o.slides:
        paras = "".join(_para(b, 2400, bullet=True) for b in s.bullets) or _para(" ", 2400)
        slides.append(_slide(_box(2, "Title", 457200, 365760, 11277600, 1005840, _para(s.title, 3600, True), "ctr")
                             + _box(3, "Body", 731520, 1600200, 10728960, 4572000, paras)))
    n = len(slides)
    master = (f'{HEAD}<p:sldMaster {NS}><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/>'
              f'</p:bgPr></p:bg><p:spTree>{GROUP}</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
              'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
              'hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
              '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="3600"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr>'
              '<a:defRPr sz="2400"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr>'
              '</p:otherStyle></p:txStyles></p:sldMaster>')
    layout = (f'{HEAD}<p:sldLayout {NS} type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>{GROUP}</p:spTree></p:cSld>'
              '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 2}"/>' for i in range(n))
    pres = (f'{HEAD}<p:presentation {NS}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            f'<p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
            '</p:presentation>')
    overrides = {"/ppt/presentation.xml": "presentationml.presentation.main+xml",
                 "/ppt/slideMasters/slideMaster1.xml": "presentationml.slideMaster+xml",
                 "/ppt/slideLayouts/slideLayout1.xml": "presentationml.slideLayout+xml", "/ppt/theme/theme1.xml": "theme+xml"}
    overrides.update({f"/ppt/slides/slide{i + 1}.xml": "presentationml.slide+xml" for i in range(n)})
    types = (f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" '
             'ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
             'ContentType="application/xml"/>' + "".join(f'<Override PartName="{k}" ContentType="{CT}{v}"/>'
                                                        for k, v in overrides.items()) + "</Types>")
    files = {"[Content_Types].xml": types,
             "_rels/.rels": _rels([("rId1", "officeDocument", "ppt/presentation.xml")]),
             "ppt/presentation.xml": pres,
             "ppt/_rels/presentation.xml.rels": _rels([("rId1", "slideMaster", "slideMasters/slideMaster1.xml")]
                                                      + [(f"rId{i + 2}", "slide", f"slides/slide{i + 1}.xml") for i in range(n)]
                                                      + [(f"rId{n + 2}", "theme", "theme/theme1.xml")]),
             "ppt/slideMasters/slideMaster1.xml": master,
             "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                                                                    ("rId2", "theme", "../theme/theme1.xml")]),
             "ppt/slideLayouts/slideLayout1.xml": layout,
             "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]),
             "ppt/theme/theme1.xml": _theme()}
    for i, xml in enumerate(slides, 1):
        files[f"ppt/slides/slide{i}.xml"] = xml
        files[f"ppt/slides/_rels/slide{i}.xml.rels"] = _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def save(folder: Path, name: str, data: bytes | str, ext: str) -> Path:
    ext = ext.lower().lstrip(".")
    if ext not in EXTENSIONS:
        raise ValueError(f"I only write: {', '.join(sorted(EXTENSIONS))}.")
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:50] or "document"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    for n in range(1000):
        path = folder / f"{slug}{'' if n == 0 else f'-{n}'}.{ext}"
        try:
            with open(path, "xb") as f:  # exclusive: an existing file is never opened for writing
                f.write(payload)
        except FileExistsError:
            continue
        os.chmod(path, 0o600)
        return path
    raise ValueError("Too many files with that name; pick another.")
