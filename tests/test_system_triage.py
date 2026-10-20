"""633 crash triage and 634 service doctor: summaries only, nothing changed."""
import pytest

from minty_core.system.runner import Result, Runner
from minty_core.system.triage import (crash_report, parse_coredumps, service_report, summarise_journal,
                                      valid_unit)

CORE = ("TIME                          PID  UID  GID SIG     COREFILE  EXE              SIZE\n"
        "Sat 2026-10-17 12:00:01 IST  4242 1000 1000 SIGSEGV present   /usr/bin/foo     5.2M\n"
        "Sun 2026-10-18 09:30:10 IST  5150 1000 1000 SIGABRT missing   /usr/lib/chrome/chrome  -\n")

JOURNAL = """2026-10-18T09:00:01+0530 arch pipewire[812]: pw.node: (alsa_output.pci-0000_00_1f.3) xrun at 192.168.1.20
2026-10-18T09:00:05+0530 arch pipewire[812]: pw.node: (alsa_output.pci-0000_00_1f.3) xrun at 192.168.1.20
2026-10-18T09:01:00+0530 arch NetworkManager[600]: <error> device (wlan0): supplicant failed
2026-10-18T09:02:00+0530 arch kernel: usb 1-2: device descriptor read/64, error -71
2026-10-18T09:03:00+0530 arch pipewire[812]: pw.node: (alsa_output.pci-0000_00_1f.3) xrun at 192.168.1.20
"""


def test_coredumps_are_parsed_into_who_when_and_how():
    rows = parse_coredumps(CORE)
    assert [(r.exe, r.signal, r.when, r.has_core) for r in rows] == [
        ("/usr/bin/foo", "SIGSEGV", "2026-10-17 12:00", True), ("/usr/lib/chrome/chrome", "SIGABRT", "2026-10-18 09:30", False)]


def test_the_journal_is_summarised_by_program_with_counts_and_scrubbed_samples():
    rows = summarise_journal(JOURNAL)
    assert rows[0].program == "pipewire" and rows[0].count == 3
    assert "192.168" not in rows[0].sample and "<ip>" in rows[0].sample
    assert {r.program for r in rows} == {"pipewire", "NetworkManager", "kernel"}


def test_unit_names_are_checked_before_they_reach_a_command():
    assert valid_unit("sshd") == "sshd.service" and valid_unit("NetworkManager.service") == "NetworkManager.service"
    assert valid_unit("user@1000.service") == "user@1000.service"
    for bad in ("", "a b", "x;rm -rf /", "../etc", "--user", "$(id)", "a" * 100):
        with pytest.raises(ValueError):
            valid_unit(bad)


class Fake:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def have(self, name):
        return True

    async def run(self, argv, timeout=None):
        self.calls.append(argv)
        for key, value in self.answers.items():
            if key in " ".join(argv):
                return value
        return Result(1, "", "unknown")


async def test_the_crash_report_names_the_program_and_stays_a_summary():
    fake = Fake({"coredumpctl": Result(0, CORE), "journalctl": Result(0, JOURNAL)})
    text = await crash_report(fake)
    assert "foo" in text and "SIGSEGV" in text and "segmentation fault" in text.lower()
    assert "pipewire" in text and "3 times" in text
    assert len(text) < 1500 and "192.168" not in text
    assert all(c[0] in ("coredumpctl", "journalctl") for c in fake.calls)
    assert fake.calls[0][:2] == ["coredumpctl", "list"]


async def test_no_crashes_is_good_news_and_missing_tools_are_said():
    quiet = Fake({"coredumpctl": Result(1, "No coredumps found.\n"), "journalctl": Result(0, "")})
    assert "No crashes" in await crash_report(quiet)

    class Gone(Fake):
        async def run(self, argv, timeout=None):
            return Result(127, "", "missing", missing=True)

    assert "not available" in await crash_report(Gone({}))


STATUS = {
    "show": Result(0, "ActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainStatus=1\nNRestarts=5\n"),
    "is-active": Result(3, "failed\n"),
    "journalctl": Result(0, "bind() failed: Address already in use\nFailed to start Foo.\n"),
}


async def test_the_service_doctor_explains_the_likely_cause_and_gives_commands_to_type():
    text = await service_report(Fake(STATUS), "foo", user=False)
    assert "failed" in text and "already in use" in text.lower() and "another program" in text.lower()
    assert "sudo systemctl restart foo.service" in text and "I did not change anything" in text
    assert "restarted 5 times" in text


async def test_with_no_name_the_doctor_lists_failed_units():
    fake = Fake({"--failed": Result(0, "foo.service loaded failed failed Foo daemon\nbar.service loaded failed failed Bar\n")})
    text = await service_report(fake, "", user=False)
    assert "foo.service" in text and "bar.service" in text and fake.calls == [["systemctl", "--failed", "--no-legend", "--plain"]]
    none = Fake({"--failed": Result(0, "")})
    assert "No services have failed" in await service_report(none, "", user=False)


async def test_a_bad_unit_name_never_reaches_a_command():
    fake = Fake({})
    with pytest.raises(ValueError):
        await service_report(fake, "x; reboot", user=False)
    assert fake.calls == []


async def test_the_runner_rules_keep_logs_and_services_read_only():
    r = Runner(which=lambda n: None)
    for bad in (["journalctl", "--vacuum-size=1M"], ["journalctl", "--rotate"], ["coredumpctl", "debug"],
                ["systemctl", "restart", "sshd"], ["systemctl", "--user", "stop", "x"], ["pacman", "-S", "x"],
                ["loginctl", "poweroff"]):
        with pytest.raises(ValueError):
            await r.run(bad)
    for good in (["journalctl", "-u", "sshd", "-n", "10"], ["coredumpctl", "list"], ["systemctl", "--user", "status", "x"]):
        assert (await r.run(good)).missing  # allowed by the rules; simply not installed in this fake
