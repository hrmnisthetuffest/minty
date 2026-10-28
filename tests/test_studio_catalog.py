"""The background-video catalogue: what category each clip is in, which one is shown today, and which clip within it.

A different style on different days (never the same one two days running while there is a choice), a clip that was not just shown,
your exclusions and favourites obeyed, thin categories skipped, and all of it still true after a restart because it is kept on disk.
"""
import random
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from minty_core.studio import catalog as cat
from minty_core.studio.assets import AssetLibrary

D0 = datetime(2026, 11, 2, 9, 0)


def day(n):
    return D0 + timedelta(days=n)


def fake_clip(tmp_path, name, body=b"x"):
    """A file the sniffer accepts as an mp4 (the catalogue does not decode video; it records and rotates)."""
    p = tmp_path / f"{name}.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + name.encode() + body * 50)
    return p


@pytest.fixture
def lib(tmp_path):
    return AssetLibrary(tmp_path / "lib")


def add(lib, tmp_path, catalog, category, n, **kw):
    out = []
    for i in range(n):
        a = lib.add(fake_clip(tmp_path, f"{category}-{i}"), "video", kw.get("license", "cc0"), source=f"test {category} {i}")
        catalog.tag(a, category, provider=kw.get("provider", "test"), external_id=f"{category}-{i}", quality=kw.get("quality", 1.0))
        out.append(a)
    return out


@pytest.fixture
def catalog(lib):
    return cat.Catalog(lib, min_clips=1)


def test_every_category_says_what_it_is_and_where_footage_may_come_from():
    assert {"parkour", "gameplay", "nature", "city", "satisfying", "abstract", "space"} <= set(cat.CATEGORIES)
    for key, c in cat.CATEGORIES.items():
        assert c.label and isinstance(c.queries, dict)
    assert cat.CATEGORIES["parkour"].queries == {} and cat.CATEGORIES["gameplay"].queries == {}  # game footage is the user's own
    assert "nasa" in cat.CATEGORIES["space"].queries and "pexels" in cat.CATEGORIES["nature"].queries


def test_an_unknown_category_is_refused(catalog, lib, tmp_path):
    a = lib.add(fake_clip(tmp_path, "z"), "video", "owned", source="me")
    with pytest.raises(cat.CatalogError, match="category"):
        catalog.tag(a, "cooking")


def test_an_empty_catalogue_has_nothing_to_choose(catalog):
    assert catalog.choose(now=D0) is None and catalog.counts() == {}


def test_a_different_style_on_different_days_and_the_same_one_all_day(catalog, lib, tmp_path):
    for c in ("nature", "city", "abstract"):
        add(lib, tmp_path, catalog, c, 3)
    seen = []
    for n in range(12):
        first = catalog.choose(now=day(n), rng=random.Random(n))
        again = catalog.choose(now=day(n), rng=random.Random(n + 100))
        assert first.category == again.category, "a day keeps its style"
        seen.append(first.category)
    assert all(a != b for a, b in zip(seen, seen[1:])), seen  # never the same style two days running
    assert set(seen) == {"nature", "city", "abstract"}


def test_the_day_is_the_same_after_a_restart(lib, tmp_path):
    c1 = cat.Catalog(lib, min_clips=1)
    for c in ("nature", "city", "abstract"):
        add(lib, tmp_path, c1, c, 2)
    picked = [c1.choose(now=day(n)).category for n in range(4)]
    c2 = cat.Catalog(AssetLibrary(tmp_path / "lib"), min_clips=1)  # a new process, the same disk
    assert [c2.choose(now=day(n)).category for n in range(4)] == picked


def test_a_clip_is_not_repeated_until_the_others_have_been_shown(catalog, lib, tmp_path):
    add(lib, tmp_path, catalog, "nature", 4)
    ids = [catalog.choose(now=D0, rng=random.Random(i)).asset.id for i in range(4)]
    assert len(set(ids)) == 4
    nxt = catalog.choose(now=D0, rng=random.Random(9)).asset.id
    assert nxt != ids[-1]


def test_one_clip_in_a_category_is_shown_again_rather_than_nothing(catalog, lib, tmp_path):
    add(lib, tmp_path, catalog, "nature", 1)
    assert catalog.choose(now=D0).asset.id == catalog.choose(now=D0).asset.id


def test_a_thin_category_is_skipped_for_one_with_enough_clips(lib, tmp_path):
    catalog = cat.Catalog(lib, min_clips=3)
    add(lib, tmp_path, catalog, "nature", 1)
    add(lib, tmp_path, catalog, "city", 4)
    picks = [catalog.choose(now=day(n)) for n in range(6)]
    assert {p.category for p in picks} == {"city"}
    assert any("only 1 clip" in p.reason for p in picks)


def test_when_every_category_is_thin_the_styles_still_take_turns(lib, tmp_path):
    catalog = cat.Catalog(lib, min_clips=5)
    add(lib, tmp_path, catalog, "nature", 1)
    add(lib, tmp_path, catalog, "city", 2)
    seen = [catalog.choose(now=day(n)).category for n in range(4)]
    assert set(seen) == {"nature", "city"} and all(a != b for a, b in zip(seen, seen[1:]))  # a little variety beats the same fullest style


def test_excluded_categories_and_clips_are_never_shown(catalog, lib, tmp_path):
    nature = add(lib, tmp_path, catalog, "nature", 3)
    add(lib, tmp_path, catalog, "city", 3)
    catalog.set_pref("exclude_categories", ["city"])
    catalog.set_pref("exclude_assets", [nature[0].id])
    for n in range(10):
        p = catalog.choose(now=day(n), rng=random.Random(n))
        assert p.category == "nature" and p.asset.id != nature[0].id


def test_favourites_are_chosen_more_often_and_a_zero_weight_is_off(catalog, lib, tmp_path):
    clips = add(lib, tmp_path, catalog, "nature", 4)
    catalog.set_pref("favourites", [clips[0].id])
    rng = random.Random(7)
    counts = {a.id: 0 for a in clips}
    for _ in range(300):
        counts[catalog.choose(now=D0, rng=rng, record=False).asset.id] += 1
    assert counts[clips[0].id] > 1.8 * max(v for k, v in counts.items() if k != clips[0].id)
    catalog.set_pref("weights", {"nature": 0})
    assert catalog.choose(now=D0) is None  # the only style is switched off


def test_a_manual_shuffle_changes_the_style_for_the_rest_of_the_day(catalog, lib, tmp_path):
    for c in ("nature", "city", "abstract"):
        add(lib, tmp_path, catalog, c, 2)
    before = catalog.choose(now=D0, rng=random.Random(1)).category
    after = catalog.shuffle(now=D0, rng=random.Random(2))
    assert after.category != before
    assert catalog.choose(now=D0, rng=random.Random(3)).category == after.category
    assert catalog.choose(now=day(1), rng=random.Random(3)).category != after.category  # tomorrow is a new day


def test_a_shuffle_with_one_style_picks_another_clip(catalog, lib, tmp_path):
    add(lib, tmp_path, catalog, "nature", 3)
    first = catalog.choose(now=D0).asset.id
    assert catalog.shuffle(now=D0).asset.id != first


def test_a_clip_whose_file_has_gone_is_dropped_and_not_chosen_again(catalog, lib, tmp_path):
    clips = add(lib, tmp_path, catalog, "nature", 2)
    Path(clips[0].path).unlink()
    for n in range(5):
        assert catalog.choose(now=D0, rng=random.Random(n)).asset.id == clips[1].id
    assert catalog.clips("nature", states=("missing",))[0].asset.id == clips[0].id


def test_a_bad_clip_can_be_marked_and_is_skipped(catalog, lib, tmp_path):
    clips = add(lib, tmp_path, catalog, "nature", 2)
    catalog.mark(clips[1].id, "bad")
    assert {catalog.choose(now=D0, rng=random.Random(n)).asset.id for n in range(6)} == {clips[0].id}


def test_the_library_stays_inside_its_size_limit_and_keeps_what_you_love(catalog, lib, tmp_path):
    found = add(lib, tmp_path, catalog, "nature", 4, provider="pexels")
    own = add(lib, tmp_path, catalog, "parkour", 1, provider="own", license="owned")[0]
    catalog.set_pref("favourites", [found[3].id])
    for a in found:  # the oldest play first
        catalog.choose(now=D0, rng=random.Random(a.id), record=True)
    size = Path(found[0].path).stat().st_size
    gone = catalog.prune(max_bytes=size * 2)
    assert len(gone) >= 2 and found[3].id not in gone and own.id not in gone
    for aid in gone:
        assert not Path(lib.get(aid).path).exists()
        assert lib.get(aid) is not None  # the record (and its licence) stays for the videos made from it
    assert Path(own.path).exists() and Path(found[3].path).exists()


def test_a_clip_already_fetched_from_a_provider_is_known(catalog, lib, tmp_path):
    add(lib, tmp_path, catalog, "nature", 1, provider="pexels")
    assert catalog.known("pexels", "nature-0") and not catalog.known("pexels", "other") and not catalog.known("nasa", "nature-0")


def test_the_summary_says_what_is_there_and_what_is_switched_off(catalog, lib, tmp_path):
    add(lib, tmp_path, catalog, "nature", 2)
    add(lib, tmp_path, catalog, "city", 1)
    catalog.set_pref("exclude_categories", ["city"])
    text = catalog.summary()
    assert "nature: 2" in text and "city: 1" in text and "off" in text and "parkour: 0" in text


def test_bad_preferences_are_refused(catalog):
    with pytest.raises(cat.CatalogError):
        catalog.set_pref("mode", "chaos")
    with pytest.raises(cat.CatalogError):
        catalog.set_pref("exclude_categories", ["cooking"])
    with pytest.raises(cat.CatalogError):
        catalog.set_pref("weights", {"nature": -1})
    with pytest.raises(cat.CatalogError):
        catalog.set_pref("nonsense", 1)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
async def test_with_nothing_in_the_catalogue_a_plain_generated_background_is_made(lib, tmp_path):
    from minty_core.studio import media

    catalog = cat.Catalog(lib, min_clips=1)
    asset = await catalog.fallback(tmp_path / "work", seconds=4)
    info = await media.probe(Path(asset.path))
    assert info.has_video and (info.width, info.height) == (720, 1280) and 3.5 < info.duration < 4.6
    assert asset.license == "generated" and catalog.counts() == {"abstract": 1}
    again = await catalog.fallback(tmp_path / "work", seconds=4)
    assert again.id == asset.id  # made once, not every day
