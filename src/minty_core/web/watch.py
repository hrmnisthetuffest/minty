"""Price, stock and page watchers (561 562 773). Polite by construction: a minimum interval with a hard floor, jitter that
only ever delays, back-off after failures, at most one request per host per pass and a gap between two requests to the
same site. Adding a watch is the per-site opt-in. Nothing here fetches by itself: the caller passes `fetch`."""
from __future__ import annotations

import random
import time
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from minty_core.web.db import WebDB
from minty_core.web.extract import changed_sentences, fingerprint, product_from_html
from minty_core.web.urls import check_web_url as _check_url

MIN_HOURS = 1.0  # nothing is ever checked more often than this, whatever the setting says
MAX_BACKOFF_H = 48.0
HOST_GAP_S = 30.0
MANUAL_GAP_S = 600.0  # "check now" twice within ten minutes answers from the last check
SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
KINDS = ("price", "stock", "page")
TERMS = ("Some sites forbid automatic checking in their terms. This sends one ordinary request per check, never more "
         "often than the interval, slows down when the site fails, and stops when you remove the watch.")


def money(price: float | None, currency: str = "") -> str:
    if price is None:
        return "no price"
    body = f"{price:,.0f}" if float(price).is_integer() else f"{price:,.2f}"
    sym = SYMBOLS.get(currency.upper(), "")
    return f"{sym}{body}" if sym or not currency else f"{body} {currency}"


class Watcher:
    def __init__(self, db: WebDB, fetch: Callable[[str], Awaitable[tuple[int, str]]],
                 clock: Callable[[], float] = time.time, min_hours: float = 6.0,
                 rng: Callable[[], float] = random.random):
        self.db, self._fetch, self._clock, self._rng = db, fetch, clock, rng
        self.min_hours = max(MIN_HOURS, float(min_hours))
        self._last_request: dict[str, float] = {}

    @staticmethod
    def terms_note() -> str:
        return TERMS

    # ---- the list -------------------------------------------------------------------------------------
    def add(self, url: str, kind: str = "price", label: str = "", hours: float | None = None,
            target: float | None = None, wish: bool = False) -> int:
        url = _check_url(url)
        if kind not in KINDS:
            raise ValueError("A watch is for a price, stock or a page.")
        every = max(self.min_hours, float(hours)) if hours else self.min_hours
        every = min(every, 24.0 * 30)
        label = " ".join(str(label or urlsplit(url).hostname).split())[:80]
        key = f"{kind}|{url}"
        with self.db.lock:
            row = self.db.db.execute("SELECT id FROM watches WHERE key=?", (key,)).fetchone()
            if row:
                self.db.db.execute("UPDATE watches SET label=?, hours=?, target=?, wish=? WHERE id=?",
                                   (label, every, target, int(wish), row["id"]))
                self.db.db.commit()
                return row["id"]
            cur = self.db.db.execute(
                "INSERT INTO watches(url, kind, label, hours, target, wish, next_check, created, key) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (url, kind, label, every, target, int(wish), self._clock(),
                                               self._clock(), key))
            self.db.db.commit()
            return cur.lastrowid

    def get(self, ident: int) -> dict | None:
        with self.db.lock:
            row = self.db.db.execute("SELECT * FROM watches WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict]:
        with self.db.lock:
            return [dict(r) for r in self.db.db.execute("SELECT * FROM watches ORDER BY id").fetchall()]

    def remove(self, ident: int) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM watches WHERE id=?", (ident,)).rowcount
            self.db.db.commit()
        return bool(n)

    def wishlist(self) -> list[dict]:
        rows = []
        for w in self.list():
            if not w["wish"]:
                continue
            gap = (w["last_price"] - w["target"]) if w["last_price"] is not None and w["target"] is not None else None
            rows.append({"id": w["id"], "name": w["label"], "price": w["last_price"], "target": w["target"],
                         "gap": gap, "currency": w["currency"], "url": w["url"],
                         "in_stock": None if w["last_stock"] is None else bool(w["last_stock"])})
        return rows

    # ---- checking -------------------------------------------------------------------------------------
    def due(self) -> list[dict]:
        now = self._clock()
        seen, out = set(), []
        for w in self.list():
            host = urlsplit(w["url"]).hostname
            if w["next_check"] > now or host in seen:
                continue
            if now - self._last_request.get(host, -1e12) < HOST_GAP_S:
                continue
            seen.add(host)
            out.append(w)
        return out

    async def run_due(self) -> list[str]:
        out: list[str] = []
        for w in self.due():
            out += await self._check(w)
        return out

    async def check_now(self, ident: int, force: bool = False) -> list[str]:
        w = self.get(ident)
        if w is None:
            return ["I have no such watch."]
        if not force and w["last_check"] is not None and self._clock() - w["last_check"] < MANUAL_GAP_S:
            mins = int((self._clock() - w["last_check"]) // 60)
            return [f"I checked {w['label']} {mins} minute{'s' * (mins != 1)} ago: "
                    f"{money(w['last_price'], w['currency'])}. Not again so soon."]
        return await self._check(w)

    def _schedule(self, w: dict, ok: bool) -> tuple[float, int]:
        now = self._clock()
        if ok:
            return now + w["hours"] * 3600 * (1 + 0.2 * self._rng()), 0
        fails = w["fails"] + 1
        return now + min(w["hours"] * 2 ** fails, MAX_BACKOFF_H) * 3600, fails

    def _save(self, ident: int, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.lock:
            self.db.db.execute(f"UPDATE watches SET {sets} WHERE id=?", (*fields.values(), ident))
            self.db.db.commit()

    async def _check(self, w: dict) -> list[str]:
        host = urlsplit(w["url"]).hostname
        self._last_request[host] = self._clock()
        try:
            status, text = await self._fetch(w["url"])
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")
        except Exception as e:  # noqa: BLE001 — any failure backs off; the reason is kept short and URL-free
            return self._failed(w, f"the site answered with an error ({type(e).__name__})")
        if w["kind"] == "page":
            return self._page(w, text)
        product = product_from_html(text)
        if product.price is None and product.in_stock is None:
            return self._failed(w, "I could not find a price or stock on that page")
        return self._product(w, product)

    def _failed(self, w: dict, why: str) -> list[str]:
        nxt, fails = self._schedule(w, False)
        self._save(w["id"], next_check=nxt, fails=fails, last_check=self._clock())
        if fails in (1, 3) and (fails == 3 or "price" in why):
            return [f"I could not check {w['label']}: {why}. I will try again more slowly."]
        return []

    def _product(self, w: dict, p) -> list[str]:
        nxt, _ = self._schedule(w, True)
        label, cur = w["label"], p.currency or w["currency"]
        stock = None if p.in_stock is None else int(p.in_stock)
        fields = dict(next_check=nxt, fails=0, last_check=self._clock(), currency=cur,
                      last_price=p.price if p.price is not None else w["last_price"],
                      last_stock=stock if stock is not None else w["last_stock"])
        msgs: list[str] = []
        first = w["last_check"] is None or (w["last_price"] is None and w["last_stock"] is None)
        if first:
            bits = [money(p.price, cur)] if p.price is not None else []
            if p.in_stock is not None:
                bits.append("in stock" if p.in_stock else "out of stock")
            msgs.append(f"Watching {label}: {', '.join(bits)}. I will look about every {w['hours']:g} hours and "
                        "tell you when it changes.")
        else:
            old = w["last_price"]
            if p.price is not None and old is not None and p.price < old and w["kind"] == "price":
                msgs.append(f"The price of {label} dropped from {money(old, cur)} to {money(p.price, cur)}.")
            if (w["kind"] == "price" and p.price is not None and w["target"] is not None and p.price <= w["target"]
                    and w["notified_price"] != p.price):
                msgs.append(f"{label} is at or below your target of {money(w['target'], cur)}: now "
                            f"{money(p.price, cur)}.")
                fields["notified_price"] = p.price
            elif p.price is not None and w["target"] is not None and p.price > w["target"]:
                fields["notified_price"] = None
            if w["last_stock"] == 0 and stock == 1:
                msgs.append(f"{label} is back in stock.")
        self._save(w["id"], **fields)
        return msgs

    def _page(self, w: dict, text: str) -> list[str]:
        digest, plain = fingerprint(text)
        nxt, _ = self._schedule(w, True)
        base = dict(next_check=nxt, fails=0, last_check=self._clock())
        if not w["last_hash"]:
            self._save(w["id"], last_hash=digest, last_text=plain[:20000], **base)
            return [f"Watching {w['label']}. I will look about every {w['hours']:g} hours and tell you when the text "
                    "changes."]
        if digest == w["last_hash"]:
            self._save(w["id"], **base)
            return []
        added, removed = changed_sentences(w["last_text"], plain)
        self._save(w["id"], last_hash=digest, last_text=plain[:20000], **base)
        trim = lambda items: " ".join(s[:160] for s in items[:2])  # noqa: E731
        parts = [f"{w['label']} changed."]
        if added:
            parts.append(f"New: {trim(added)}")
        if removed:
            parts.append(f"Gone: {trim(removed)}")
        return [" ".join(parts)]
