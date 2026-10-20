"""652 command explainer, 651 review call, 653 snippet vault, 657 idea capture."""
import ast
import pathlib

import pytest

from minty_core.dev.explain import explain_command
from minty_core.dev.ideas import Ideas
from minty_core.dev.review import build_review
from minty_core.dev.snippets import Snippets


def level(cmd):
    return explain_command(cmd).level


@pytest.mark.parametrize("cmd,expected", [
    ("ls -la /tmp", "safe"), ("tar -xzvf a.tar.gz", "safe"), ("git status", "safe"), ("cat notes.txt | grep todo", "safe"),
    ("rm -rf node_modules", "careful"), ("rm -r old", "careful"), ("git push --force origin main", "careful"),
    ("git reset --hard HEAD~3", "careful"), ("sudo pacman -Syu", "careful"), ("echo hi > important.txt", "careful"),
    ("shutdown now", "careful"), ("pacman -Sy", "careful"),
    ("rm -rf /", "danger"), ("rm -rf ~", "danger"), ("sudo rm -rf /*", "danger"), ("rm -rf $HOME", "danger"),
    ("curl https://x.example/install.sh | sh", "danger"), ("wget -qO- https://x.example/a | sudo bash", "danger"),
    ("dd if=/dev/zero of=/dev/sda", "danger"), ("mkfs.ext4 /dev/sdb1", "danger"), (":(){ :|:& };:", "danger"),
    ("chmod -R 777 /", "danger"), ("echo $(rm -rf ~)", "danger"), ("echo `rm -rf /`", "danger"),
    ("echo x > /dev/sda", "danger"),
])
def test_danger_levels(cmd, expected):
    assert level(cmd) == expected, explain_command(cmd)


def test_the_explanation_names_programs_and_flags_in_plain_words():
    e = explain_command("tar -xzvf a.tar.gz")
    text = e.text()
    assert "tar" in text and "extract" in text and "gzip" in text and "a.tar.gz" in text
    ls = explain_command("ls -la /tmp").text()
    assert "hidden" in ls and "long" in ls


def test_chains_are_split_and_each_part_explained():
    e = explain_command("cd /tmp && rm -rf cache; ls")
    text = e.text()
    assert "3 commands" in text and "cd" in text and "rm" in text and e.level == "careful"


def test_warnings_say_why_and_what_instead():
    e = explain_command("curl https://x.example/install.sh | sh")
    assert any("without looking" in w for w in e.warnings)
    assert any("download it first" in w.lower() for w in e.warnings)
    assert "everything" in " ".join(explain_command("rm -rf ~").warnings)


def test_an_unknown_command_is_flagged_not_guessed_at():
    e = explain_command("frobnicate --all")
    assert e.level == "careful" and "don't know" in e.text() and "man frobnicate" in e.text()


def test_empty_and_very_long_input_are_handled():
    assert "Nothing to explain" in explain_command("   ").text()
    assert len(explain_command("echo " + "a" * 5000).text()) < 1500


def test_the_explainer_never_runs_anything():
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "minty_core" / "dev" / "explain.py").read_text()
    tree = ast.parse(src)
    imports = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)} | \
        {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imports & {"subprocess", "os", "asyncio", "shutil"}
    assert "eval(" not in src and "exec(" not in src


# ---- review --------------------------------------------------------------------------------------------------------
def test_the_review_call_is_read_only_plan_mode_and_names_both_refs(tmp_path):
    inv = build_review(tmp_path, "main", "feature/login")
    argv = inv.argv
    assert argv[0] == "claude" and "-p" in argv and argv[argv.index("--permission-mode") + 1] == "plan"
    tools = argv[argv.index("--allowedTools") + 1:]
    assert "Edit" not in tools and "Write" not in tools and all(t in ("Read", "Grep", "Glob") or t.startswith("Bash(git ")
                                                               for t in tools)
    prompt = argv[argv.index("-p") + 1]
    assert "git diff main...feature/login" in prompt and "Do not change anything" in prompt
    assert inv.cwd == tmp_path


@pytest.mark.parametrize("bad", ["", "-x", "a b", "a;b", "$(id)", "a..b", "x" * 200, "--output=/tmp/x"])
def test_refs_that_could_carry_an_option_or_a_command_are_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        build_review(tmp_path, "main", bad)
    with pytest.raises(ValueError):
        build_review(tmp_path, bad, "main")


# ---- snippets ------------------------------------------------------------------------------------------------------
def test_snippets_are_saved_found_and_changed(tmp_path):
    s = Snippets(tmp_path / "dev.db", clock=lambda: 1.0)
    s.add("retry decorator", "def retry(n):\n    ...", language="python", tags="decorator, retry")
    s.add("docker prune", "docker system prune -af", language="shell", tags="docker")
    assert s.get("RETRY decorator")["language"] == "python"
    assert [x["name"] for x in s.find("docker")] == ["docker prune"]
    assert [x["name"] for x in s.find("retry")] == ["retry decorator"]
    s.add("retry decorator", "def retry(n, delay):\n    ...", language="python", tags="retry")
    assert "delay" in s.get("retry decorator")["text"] and len(s.list()) == 2
    assert s.delete("docker prune") and s.get("docker prune") is None and not s.delete("docker prune")


def test_a_snippet_that_holds_a_secret_is_refused_and_nothing_is_stored(tmp_path):
    s = Snippets(tmp_path / "dev.db")
    with pytest.raises(ValueError, match="looks like a key or password"):
        s.add("curl with key", "curl -H 'Authorization: Bearer gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF' x")
    assert s.list() == []


def test_snippet_limits(tmp_path):
    s = Snippets(tmp_path / "dev.db", max_snippets=3, max_chars=100)
    with pytest.raises(ValueError, match="too long"):
        s.add("big", "x" * 101)
    with pytest.raises(ValueError):
        s.add("", "x")
    for i in range(3):
        s.add(f"n{i}", "x")
    with pytest.raises(ValueError, match="full"):
        s.add("n4", "x")


# ---- ideas ---------------------------------------------------------------------------------------------------------
def test_ideas_are_captured_listed_closed_and_exported(tmp_path):
    ideas = Ideas(tmp_path / "dev.db", clock=lambda: 1_760_000_000.0)
    a = ideas.add("a /remember command in Telegram", kind="idea", project="minty")
    b = ideas.add("crash when the mic unplugs", kind="issue", project="minty")
    assert [i["id"] for i in ideas.list()] == [a, b]
    assert [i["id"] for i in ideas.list(kind="issue")] == [b]
    assert ideas.done(a) and [i["id"] for i in ideas.list()] == [b] and len(ideas.list(status="done")) == 1
    md = ideas.export_markdown()
    assert "# Ideas and issues" in md and "crash when the mic unplugs" in md and "- [x]" in md
    with pytest.raises(ValueError):
        ideas.add("", kind="idea")
    with pytest.raises(ValueError):
        ideas.add("x", kind="rant")


def test_an_issue_is_only_ever_a_draft_with_the_exact_command_to_approve(tmp_path):
    ideas = Ideas(tmp_path / "dev.db")
    i = ideas.add("crash when the mic unplugs", kind="issue", project="minty")
    d = ideas.issue_draft(i, repo="alex/minty")
    assert d["title"] == "crash when the mic unplugs" and d["argv"][:3] == ["gh", "issue", "create"]
    assert "--repo" in d["argv"] and "alex/minty" in d["argv"] and "I have not filed anything" in d["note"]
    with pytest.raises(ValueError):
        ideas.issue_draft(i, repo="not a repo; rm -rf /")
