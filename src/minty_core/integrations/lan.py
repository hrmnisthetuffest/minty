"""A local-network inventory (689), on request only, on private ranges you list. It sends one ping to each address in those
ranges and reads the system's neighbour table; it keeps a list so a NEW device is noticed. It never scans public addresses, never
scans more than ~1000 addresses, never runs on its own, and cannot be asked again within ten minutes."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from minty_core import osutil
from minty_core.integrations.db import IntegrationsDB
from minty_core.system.runner import Runner

PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
MIN_PREFIX, MAX_HOSTS = 22, 1024
_NEIGH = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s+dev\s+\S+\s+lladdr\s+([0-9a-f:]{17})\s+(\w+)\s*$", re.I)


def _private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and any(addr in n for n in PRIVATE)


def _ping_rule(a: list[str]) -> bool:
    return len(a) == 6 and a[1:5] == ["-c", "1", "-W", "1"] and _private(a[5])


def make_runner() -> Runner:
    return Runner(allowed={"ping", "ip"}, rules={"ping": _ping_rule, "ip": lambda a: a[1:] == ["neigh", "show"]}, timeout=5.0)


def parse_ranges(ranges: list[str]) -> list[ipaddress.IPv4Network]:
    if not ranges:
        raise ValueError("Tell me which network ranges to look at, for example 192.168.1.0/24 (integrations.lan_ranges).")
    nets, hosts = [], 0
    for r in ranges:
        try:
            net = ipaddress.ip_network(str(r).strip(), strict=False)
        except ValueError:
            raise ValueError(f"{r!r} is not a network range.") from None
        if net.version != 4 or not any(net.subnet_of(p) for p in PRIVATE):
            raise ValueError(f"{r} is not a private home-network range (10.x, 172.16-31.x or 192.168.x), so I will not scan it.")
        if net.prefixlen < MIN_PREFIX:
            raise ValueError(f"{r} is too wide; use /22 or narrower (at most about 1000 addresses).")
        hosts += net.num_addresses
        nets.append(net)
    if hosts > MAX_HOSTS:
        raise ValueError("That is more than about 1000 addresses in total; list fewer ranges.")
    return nets


@dataclass
class ScanResult:
    devices: list[dict] = field(default_factory=list)
    new: list[dict] = field(default_factory=list)
    first: bool = False
    ranges: list[str] = field(default_factory=list)

    def text(self) -> str:
        n = len(self.devices)
        out = [f"Found {n} device{'s' * (n != 1)} on {', '.join(self.ranges)}."]
        if self.devices:
            out.append("Devices: " + ", ".join(f"{d['ip']} ({d['label'] or d['mac']})" for d in self.devices[:20]) + ".")
        if self.first:
            out.append("This was the first scan, so everything looks new; from now on I will point out anything that appears.")
        elif self.new:
            out.append("New since the last scan: " + ", ".join(f"{d['ip']} ({d['mac']})" for d in self.new) + ".")
        else:
            out.append("Nothing new since the last scan.")
        named = [f"{d['label']} at {d['ip']}" for d in self.devices if d["label"]]
        if named:
            out.append("Known: " + ", ".join(named) + ".")
        out.append("Phones often use a different private address on each Wi-Fi, so an unfamiliar one is not always a stranger.")
        return " ".join(out)


class Lan:
    def __init__(self, runner, db: IntegrationsDB, clock: Callable[[], float] = time.time, min_gap_s: float = 600.0):
        self.r, self.db, self._clock, self.min_gap = runner, db, clock, max(600.0, float(min_gap_s))

    async def scan(self, ranges: list[str]) -> ScanResult:
        if osutil.IS_WINDOWS:  # it runs `ping -c` and `ip neigh`, which Windows does not have in that form
            raise ValueError("Scanning the home network works on Linux only for now.")
        nets = parse_ranges(ranges)
        now = self._clock()
        last = float(self.db.meta("lan_last", "0") or 0)
        if last and now - last < self.min_gap:
            mins = max(1, int((now - last) // 60))
            raise ValueError(f"I scanned {'a minute' if mins == 1 else f'{mins} minutes'} ago; that is too soon to scan again.")
        self.db.set_meta("lan_last", str(now))
        gate = asyncio.Semaphore(16)

        async def ping(ip) -> None:
            async with gate:
                await self.r.run(["ping", "-c", "1", "-W", "1", str(ip)])

        await asyncio.gather(*(ping(ip) for net in nets for ip in net.hosts()))
        table = await self.r.run(["ip", "neigh", "show"])
        seen: dict[str, str] = {}
        for line in table.out.splitlines():
            m = _NEIGH.match(line.strip())
            if m and m.group(3).upper() not in ("FAILED", "INCOMPLETE") and any(ipaddress.ip_address(m.group(1)) in n for n in nets):
                seen[m.group(2).lower()] = m.group(1)
        res = ScanResult(ranges=[str(n) for n in nets])
        with self.db.lock:
            res.first = self.db.db.execute("SELECT COUNT(*) FROM lan_devices").fetchone()[0] == 0
            for mac, ip in sorted(seen.items(), key=lambda kv: ipaddress.ip_address(kv[1])):
                row = self.db.db.execute("SELECT label FROM lan_devices WHERE mac=?", (mac,)).fetchone()
                if row is None:
                    self.db.db.execute("INSERT INTO lan_devices(mac, ip, first_seen, last_seen) VALUES (?,?,?,?)", (mac, ip, now, now))
                else:
                    self.db.db.execute("UPDATE lan_devices SET ip=?, last_seen=? WHERE mac=?", (ip, now, mac))
                dev = {"ip": ip, "mac": mac, "label": row["label"] if row else ""}
                res.devices.append(dev)
                if row is None:
                    res.new.append(dev)
            self.db.db.commit()
        return res

    def name(self, mac: str, label: str) -> bool:
        with self.db.lock:
            n = self.db.db.execute("UPDATE lan_devices SET label=?, known=1 WHERE mac=?",
                                   (" ".join(str(label).split())[:60], str(mac).lower())).rowcount
            self.db.db.commit()
        return bool(n)
