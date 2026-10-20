"""663 image generation, 664 translation glossary, 665 claim evidence."""
import base64
import json
import time

import pytest

from minty_core.create.db import CreateDB
from minty_core.create.evidence import find_evidence, format_evidence
from minty_core.create.images import ImageGen
from minty_core.create.translate import TranslationGlossary

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ACCOUNT = "a" * 32


class Cloud:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body, []

    async def post(self, url, headers, body):
        self.calls.append((url, headers, body))
        if self.body is not None:
            return self.status, self.body
        return self.status, {"success": True, "result": {"image": base64.b64encode(PNG).decode()}}


def gen(tmp_path, cloud, keys=("tok", ACCOUNT), cap=3, clock=None):
    secrets = {"cf_token": keys[0], "cf_account": keys[1]} if keys else {}
    return ImageGen(cloud.post, lambda n: secrets.get(n), CreateDB(tmp_path / "c.db"), tmp_path / "images",
                    clock=clock or (lambda: 1_760_000_000.0), daily_cap=cap)


async def test_an_image_is_made_saved_under_a_new_name_and_the_allowance_is_counted(tmp_path):
    cloud = Cloud()
    g = gen(tmp_path, cloud)
    first = await g.generate("a red fox reading a book, watercolour")
    second = await g.generate("a red fox reading a book, watercolour")
    assert first.read_bytes() == PNG and first != second and first.suffix == ".png"
    url, headers, body = cloud.calls[0]
    assert url == f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    assert headers["Authorization"] == "Bearer tok" and body["prompt"].startswith("a red fox") and body["steps"] == 4
    assert g.left() == 1 and oct(first.stat().st_mode & 0o777) == "0o600"


async def test_the_daily_cap_stops_requests_before_they_are_sent_and_resets_tomorrow(tmp_path):
    cloud = Cloud()
    t = [1_760_000_000.0]
    g = gen(tmp_path, cloud, cap=2, clock=lambda: t[0])
    await g.generate("one")
    await g.generate("two")
    with pytest.raises(ValueError, match="used today"):
        await g.generate("three")
    assert len(cloud.calls) == 2
    t[0] += 86400 * 1.1
    await g.generate("tomorrow")
    assert len(cloud.calls) == 3


async def test_missing_keys_say_how_to_get_them_and_send_nothing(tmp_path):
    cloud = Cloud()
    with pytest.raises(ValueError, match="set-key plugin.create.cf_token"):
        await gen(tmp_path, cloud, keys=None).generate("a fox")
    with pytest.raises(ValueError, match="account"):
        await gen(tmp_path, cloud, keys=("tok", "not-an-account-id")).generate("a fox")
    assert cloud.calls == []


@pytest.mark.parametrize("prompt", ["", "x" * 500, "draw gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF",
                                    "my card is 4111 1111 1111 1111 draw it"])
async def test_prompts_with_secrets_or_ids_or_the_wrong_size_are_refused_unsent(tmp_path, prompt):
    cloud = Cloud()
    with pytest.raises(ValueError):
        await gen(tmp_path, cloud).generate(prompt)
    assert cloud.calls == []


async def test_service_trouble_is_reported_plainly_and_does_not_use_up_the_count(tmp_path):
    for status, word in ((429, "allowance"), (401, "rejected"), (500, "could not")):
        g = gen(tmp_path / str(status), Cloud(status, {"success": False}))
        with pytest.raises(ValueError, match=word):
            await g.generate("a fox")
        assert g.left() == 3
    bad = gen(tmp_path / "bad", Cloud(200, {"success": True, "result": {"image": "!!!notbase64"}}))
    with pytest.raises(ValueError, match="could not"):
        await bad.generate("a fox")


# ---- translation glossary -------------------------------------------------------------------------------------------
def test_the_glossary_says_which_terms_a_translation_must_use(tmp_path):
    g = TranslationGlossary(CreateDB(tmp_path / "c.db"))
    g.add("Minty", "hi", "मिंटी")
    g.add("school bus", "hindi", "स्कूल बस")
    g.add("Minty", "pa", "ਹੇਡੀਜ਼")
    assert sorted(t for t, _ in g.terms("hi")) == ["Minty", "school bus"]
    prep = g.prepare("The school bus will wait. Minty is my assistant.", "Hindi")
    assert prep.language == "hi" and dict(prep.constraints) == {"Minty": "मिंटी", "school bus": "स्कूल बस"}
    assert "मिंटी" in prep.text and "Translate" in prep.text
    assert g.prepare("Nothing special here.", "hi").constraints == []


def test_the_check_names_the_terms_the_translation_missed(tmp_path):
    g = TranslationGlossary(CreateDB(tmp_path / "c.db"))
    g.add("Minty", "hi", "मिंटी")
    src = "Minty will call you."
    assert g.check(src, "मिंटी आपको फोन करेगा।", "hi") == []
    problems = g.check(src, "मिन्टी आपको फोन करेगा।", "hi")
    assert problems == ["'Minty' should be 'मिंटी'"]


def test_glossary_rules(tmp_path):
    g = TranslationGlossary(CreateDB(tmp_path / "c.db"))
    for args in (("", "hi", "x"), ("a", "hi", ""), ("a" * 100, "hi", "x"), ("a", "klingon-with-a-very-long-name-here", "x")):
        with pytest.raises(ValueError):
            g.add(*args)
    g.add("bus", "hi", "बस")
    g.add("bus", "hi", "बस गाड़ी")
    assert g.terms("hi") == [("bus", "बस गाड़ी")] and g.remove("bus", "hi") and g.terms("hi") == []


# ---- claim evidence ------------------------------------------------------------------------------------------------
WIKI = json.dumps({"query": {"search": [
    {"title": "Great Wall of China", "snippet": "The Great Wall of China is about 21,196 km long in total.",
     "timestamp": "2026-01-02T00:00:00Z"}]}})
ARXIV = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2610.1v1</id>
<published>2026-05-01T00:00:00Z</published><title>Surveying the wall</title>
<summary>We measured the wall again. Our survey finds a length of 13,171 miles for all sections. Unrelated sentence here.</summary>
</entry></feed>"""


async def fake_get(url, params):
    if "wikipedia" in url:
        return WIKI
    if "arxiv" in url:
        return ARXIV
    raise RuntimeError("provider down")


async def test_evidence_shows_sources_with_verbatim_quotes_and_flags_different_numbers_without_a_verdict():
    ev = await find_evidence("The Great Wall of China is 21,196 km long", fake_get, providers=("wikipedia", "arxiv", "pubmed"))
    assert {e.provider for e in ev.items} == {"wikipedia", "arxiv"} and ev.failed == ["pubmed"]
    for item in ev.items:
        assert item.quote in item.raw_text  # a quote is a sentence copied word for word from the source text
    text = format_evidence(ev)
    assert "21,196" in text and "13,171" in text and "Different numbers" in text
    assert "I show what the sources say" in text and "https://en.wikipedia.org/wiki/Great_Wall_of_China" in text
    assert not any(w in text.lower() for w in (" is true", " is false", "debunked", "confirmed", "verdict"))


async def test_nothing_found_and_empty_claims_are_said_plainly():
    async def none(url, params):
        return json.dumps({"query": {"search": []}}) if "wikipedia" in url else "<feed xmlns='http://www.w3.org/2005/Atom'/>"

    ev = await find_evidence("Zorblax frobnicates quietly", none, providers=("wikipedia", "arxiv"))
    assert "found nothing" in format_evidence(ev).lower()
    with pytest.raises(ValueError):
        await find_evidence("  ", none)
    with pytest.raises(ValueError):
        await find_evidence("x" * 600, none)
