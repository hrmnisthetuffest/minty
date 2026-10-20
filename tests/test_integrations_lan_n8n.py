"""689 local network inventory (on request, private ranges you list), 690 n8n workflow templates."""
import json

import pytest

from minty_core.integrations.db import IntegrationsDB
from minty_core.integrations.lan import Lan, make_runner, parse_ranges
from minty_core.integrations.n8n_templates import NAMES, build_template
from minty_core.system.runner import Result

NEIGH = """192.168.1.1 dev wlan0 lladdr aa:bb:cc:00:00:01 REACHABLE
192.168.1.20 dev wlan0 lladdr aa:bb:cc:00:00:20 STALE
192.168.1.77 dev wlan0  FAILED
192.168.1.99 dev wlan0 lladdr aa:bb:cc:00:00:99 REACHABLE
10.5.5.5 dev eth0 lladdr aa:bb:cc:00:05:05 REACHABLE
fe80::1 dev wlan0 lladdr aa:bb:cc:00:00:01 router STALE
"""


@pytest.mark.parametrize("ok", [["192.168.1.0/24"], ["10.0.0.0/24", "172.16.4.0/24"], ["192.168.0.0/22"], ["192.168.1.0/28"]])
def test_private_ranges_of_a_sensible_size_are_accepted(ok):
    assert parse_ranges(ok)


@pytest.mark.parametrize("bad", [[], ["8.8.8.0/24"], ["0.0.0.0/0"], ["10.0.0.0/8"], ["192.168.0.0/16"], ["172.32.0.0/24"],
                                 ["169.254.0.0/24"], ["127.0.0.0/24"], ["100.64.0.0/24"], ["2001:db8::/64"], ["fe80::/64"],
                                 ["not a range"], ["192.168.1.0/24; reboot"], ["192.168.1.0/24", "1.1.1.0/24"]])
def test_public_wide_odd_and_ipv6_ranges_are_refused(bad):
    with pytest.raises(ValueError):
        parse_ranges(bad)


class Net:
    def __init__(self, neigh=NEIGH):
        self.calls, self.neigh = [], neigh

    def have(self, name):
        return True

    async def run(self, argv, timeout=None):
        self.calls.append(argv)
        if argv[:3] == ["ip", "neigh", "show"]:
            return Result(0, self.neigh)
        return Result(0, "")


async def test_a_scan_pings_only_the_listed_hosts_and_reads_the_neighbour_table(tmp_path):
    net = Net()
    lan = Lan(net, IntegrationsDB(tmp_path / "i.db"), clock=lambda: 1000.0)
    res = await lan.scan(["192.168.1.0/29"])
    pings = [c[-1] for c in net.calls if c[0] == "ping"]
    assert sorted(pings) == [f"192.168.1.{i}" for i in range(1, 7)]  # a /29: six usable hosts, none outside
    assert all(c[:5] == ["ping", "-c", "1", "-W", "1"] for c in net.calls if c[0] == "ping")
    assert [d["ip"] for d in res.devices] == ["192.168.1.1"]  # .20 and .99 are real neighbours but outside this /29


async def test_only_devices_inside_the_listed_ranges_count_and_failed_entries_are_ignored(tmp_path):
    lan = Lan(Net(), IntegrationsDB(tmp_path / "i.db"), clock=lambda: 1000.0)
    res = await lan.scan(["192.168.1.0/24"])
    ips = sorted(d["ip"] for d in res.devices)
    assert ips == ["192.168.1.1", "192.168.1.20", "192.168.1.99"]  # not 10.5.5.5, not the FAILED one, not IPv6


async def test_a_new_device_is_noticed_once_and_naming_it_makes_it_known(tmp_path):
    net = Net()
    t = [1000.0]
    lan = Lan(net, IntegrationsDB(tmp_path / "i.db"), clock=lambda: t[0], min_gap_s=600)
    first = await lan.scan(["192.168.1.0/24"])
    assert len(first.new) == 3 and "3 devices" in first.text()
    t[0] += 700
    net.neigh = NEIGH + "192.168.1.150 dev wlan0 lladdr aa:bb:cc:00:01:50 REACHABLE\n"
    second = await lan.scan(["192.168.1.0/24"])
    assert [d["ip"] for d in second.new] == ["192.168.1.150"] and "New since the last scan: 192.168.1.150" in second.text()
    assert lan.name("aa:bb:cc:00:01:50", "Dad's phone") and lan.name("nope", "x") is False
    t[0] += 700
    third = await lan.scan(["192.168.1.0/24"])
    assert third.new == [] and "Dad's phone" in third.text()


async def test_a_second_scan_too_soon_does_not_touch_the_network(tmp_path):
    net = Net()
    t = [1000.0]
    lan = Lan(net, IntegrationsDB(tmp_path / "i.db"), clock=lambda: t[0], min_gap_s=600)
    await lan.scan(["192.168.1.0/29"])
    n = len(net.calls)
    t[0] += 60
    with pytest.raises(ValueError, match="scanned a minute ago|too soon"):
        await lan.scan(["192.168.1.0/29"])
    assert len(net.calls) == n


async def test_the_runner_refuses_pings_outside_private_space_and_any_other_ip_command():
    r = make_runner()
    for bad in (["ping", "-c", "1", "-W", "1", "8.8.8.8"], ["ping", "-c", "100", "-W", "1", "192.168.1.1"],
                ["ping", "-f", "192.168.1.1"], ["ping", "-c", "1", "-W", "1", "192.168.1.1; reboot"],
                ["ping", "-c", "1", "-W", "1", "127.0.0.1"], ["ip", "route", "add", "x"], ["ip", "neigh", "flush", "all"],
                ["ping", "-c", "1", "-W", "1", "169.254.1.1"], ["nmap", "192.168.1.0/24"]):
        with pytest.raises(ValueError):
            await r.run(bad)
    for good in (["ping", "-c", "1", "-W", "1", "192.168.1.1"], ["ping", "-c", "1", "-W", "1", "10.2.3.4"],
                 ["ip", "neigh", "show"]):
        r._check(good)


# ---- n8n templates -------------------------------------------------------------------------------------------------
def test_each_template_is_valid_json_inactive_and_internally_consistent():
    assert set(NAMES) == {"daily-weather-to-telegram", "rss-to-telegram", "webhook-to-telegram"}
    for name in NAMES:
        wf = json.loads(build_template(name, chat_id="123456789", url="https://feeds.example/rss", city_lat=30.7, city_lon=76.7))
        assert wf["active"] is False and wf["name"] and wf["nodes"]
        names = {n["name"] for n in wf["nodes"]}
        assert len(names) == len(wf["nodes"])
        for src, outs in wf["connections"].items():
            assert src in names
            for branch in outs["main"]:
                for link in branch:
                    assert link["node"] in names
        types = {n["type"] for n in wf["nodes"]}
        assert not types & {"n8n-nodes-base.code", "n8n-nodes-base.executeCommand", "n8n-nodes-base.function",
                            "n8n-nodes-base.ssh", "n8n-nodes-base.readWriteFile"}
        assert all("password" not in json.dumps(n.get("credentials", {})).lower() for n in wf["nodes"])


def test_template_parameters_are_checked_before_they_reach_the_workflow():
    assert "123456789" in build_template("rss-to-telegram", chat_id="123456789", url="https://feeds.example/rss")
    for kwargs in ({"chat_id": "abc"}, {"chat_id": "12; drop"}, {"chat_id": "1" * 30}):
        with pytest.raises(ValueError):
            build_template("webhook-to-telegram", **kwargs)
    with pytest.raises(ValueError):
        build_template("rss-to-telegram", chat_id="123", url="http://insecure.example/rss")
    with pytest.raises(ValueError):
        build_template("rss-to-telegram", chat_id="123", url="https://user:pw@feeds.example/rss")
    with pytest.raises(ValueError):
        build_template("daily-weather-to-telegram", chat_id="123", city_lat=999, city_lon=0)
    with pytest.raises(ValueError, match="no template"):
        build_template("format-disk", chat_id="123")
