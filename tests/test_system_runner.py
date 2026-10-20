import pytest

from minty_core.system.runner import Runner


async def test_an_allowed_program_runs_without_a_shell():
    r = Runner(allowed={"echo"})
    out = await r.run(["echo", "hello; rm -rf /"])
    assert out.code == 0 and out.out.strip() == "hello; rm -rf /" and not out.missing


async def test_a_program_outside_the_list_is_refused_before_anything_runs():
    r = Runner(allowed={"echo"})
    with pytest.raises(ValueError, match="not on the list"):
        await r.run(["rm", "-rf", "/tmp/x"])
    with pytest.raises(ValueError):
        await r.run([])
    with pytest.raises(ValueError):
        await r.run(["/bin/echo", "x"])  # a path is not a name on the list


async def test_a_missing_program_is_a_result_not_an_exception():
    r = Runner(allowed={"definitely-not-installed-xyz"})
    out = await r.run(["definitely-not-installed-xyz", "--help"])
    assert out.missing and out.code == 127


async def test_a_slow_command_is_stopped_and_output_is_capped():
    r = Runner(allowed={"sleep", "head"}, timeout=0.3)
    out = await r.run(["sleep", "5"])
    assert out.timed_out and out.code != 0
    big = Runner(allowed={"head"}, max_out=100)
    assert len((await big.run(["head", "-c", "5000", "/dev/zero"])).out) <= 100


async def test_the_environment_is_plain_and_carries_no_secrets(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret")
    r = Runner(allowed={"env"})
    out = (await r.run(["env"])).out
    assert "gsk_secret" not in out and "LC_ALL=C" in out
