"""Simulated market feed + AI signal generation engine.

Prices follow a bounded random walk per symbol. Technical indicators
(RSI / EMA / MACD) are computed over the simulated series and blended
into a directional score, which drives the "AI" signal decisions.
"""
import random
import threading
import time
from datetime import datetime, timezone

from db import db_execute, db_get, db_query, get_setting, now_iso

PAIRS = {
    "BTC/USDT": {"base": 64231.0, "vol": 0.0011, "dp": 1},
    "ETH/USDT": {"base": 3417.5, "vol": 0.0014, "dp": 2},
    "SOL/USDT": {"base": 172.42, "vol": 0.0019, "dp": 2},
    "EUR/USD": {"base": 1.0864, "vol": 0.00035, "dp": 5},
    "GBP/USD": {"base": 1.2742, "vol": 0.00035, "dp": 5},
    "XAU/USD": {"base": 2389.7, "vol": 0.0007, "dp": 2},
}

TIMEFRAMES = ["5m", "15m", "1h", "4h"]
MAX_HISTORY = 150
TICK_SECONDS = 2.5
RISK_MULT = {"conservative": 0.8, "balanced": 1.0, "aggressive": 1.35}


# ----------------------------- indicators -----------------------------
def ema(values, period):
    if len(values) < period:
        return values[-1:] or [0.0]
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    out = [e]
    for v in values[period:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(bars, period=14):
    rng = [b["h"] - b["l"] for b in bars[-period:]]
    return sum(rng) / len(rng) if rng else 0.0


# ------------------------------ engine --------------------------------
class MarketEngine:
    def __init__(self):
        self.prices = {}
        self.history = {}
        self.lock = threading.Lock()
        self._thread = None
        for pair, meta in PAIRS.items():
            price = meta["base"] * random.uniform(0.985, 1.015)
            self.prices[pair] = price
            self.history[pair] = self._seed_history(price, meta["vol"])

    def _seed_history(self, price, vol):
        bars = []
        p = price * random.uniform(0.99, 1.01)
        t = time.time() - MAX_HISTORY * 30  # simulated 30s bars
        for _ in range(MAX_HISTORY):
            o = p
            drift = random.gauss(0.0, vol) * p
            c = max(o + drift, p * 0.9)
            h = max(o, c) * (1 + abs(random.gauss(0.0, vol / 2)))
            lo = min(o, c) * (1 - abs(random.gauss(0.0, vol / 2)))
            bars.append({"t": int(t), "o": o, "h": h, "l": lo, "c": c})
            p = c
            t += 30
        return bars

    # ------------------------------ feed ------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as exc:  # pragma: no cover - keep thread alive
                print("[engine] tick error:", exc)
            time.sleep(TICK_SECONDS)

    def tick(self):
        with self.lock:
            for pair, meta in PAIRS.items():
                price = self.prices[pair]
                drift = random.gauss(0.0, meta["vol"]) * price
                # gentle mean reversion so prices stay believable
                drift += (meta["base"] - price) * 0.0004
                new_price = max(price + drift, meta["base"] * 0.5)
                self.prices[pair] = new_price

                bars = self.history[pair]
                last = bars[-1]
                if int(time.time()) - last["t"] >= 30:  # new bar every 30s
                    bars.append(
                        {"t": int(time.time()), "o": price, "h": max(price, new_price),
                         "l": min(price, new_price), "c": new_price}
                    )
                    if len(bars) > MAX_HISTORY:
                        del bars[: len(bars) - MAX_HISTORY]
                else:
                    last["c"] = new_price
                    last["h"] = max(last["h"], new_price)
                    last["l"] = min(last["l"], new_price)

        self._settle_signals()
        self._maybe_autogenerate()

    # --------------------------- signal ops ---------------------------
    def _settle_signals(self):
        with self.lock:
            active = db_query("SELECT * FROM signals WHERE status = 'ACTIVE'")
            for sig in active:
                price = self.prices.get(sig["pair"])
                if price is None:
                    continue
                hit = None
                if sig["direction"] == "LONG" and price >= sig["take_profit"]:
                    hit = "TP_HIT"
                elif sig["direction"] == "LONG" and price <= sig["stop_loss"]:
                    hit = "SL_HIT"
                elif sig["direction"] == "SHORT" and price <= sig["take_profit"]:
                    hit = "TP_HIT"
                elif sig["direction"] == "SHORT" and price >= sig["stop_loss"]:
                    hit = "SL_HIT"
                if hit:
                    db_execute(
                        "UPDATE signals SET status = ?, closed_at = ? WHERE id = ?",
                        (hit, now_iso(), sig["id"]),
                    )

    def _maybe_autogenerate(self):
        if get_setting("ai_auto", "1") != "1":
            return
        try:
            max_active = int(get_setting("max_active", "6"))
        except ValueError:
            max_active = 6
        row = db_get("SELECT COUNT(*) AS n FROM signals WHERE status = 'ACTIVE'")
        if row["n"] >= max_active:
            return
        if random.random() < 0.10:
            self.generate_signal()

    def generate_signal(self, pair=None):
        with self.lock:
            pair = pair or random.choice(list(PAIRS.keys()))
            meta = PAIRS[pair]
            bars = self.history[pair]
            closes = [b["c"] for b in bars]
            if len(closes) < 35:
                return None

            price = self.prices[pair]
            r = rsi(closes)
            fast = ema(closes, 9)[-1]
            slow = ema(closes, 21)[-1]
            macd = ema(closes, 12)[-1] - ema(closes, 26)[-1]
            macd_prev = ema(closes[:-1], 12)[-1] - ema(closes[:-1], 26)[-1] \
                if len(closes) > 35 else macd

            score = 0
            score += 2 if fast > slow else -2
            score += 1.5 if macd > 0 else -1.5
            score += 1 if macd > macd_prev else -1
            if r < 32:
                score += 2
            elif r > 68:
                score -= 2

            direction = "LONG" if score >= 0 else "SHORT"
            confidence = round(min(97.0, max(55.0, 60 + abs(score) * 5.5 + random.uniform(0, 9))), 1)

            risk = RISK_MULT.get(get_setting("risk", "balanced"), 1.0)
            vol = atr(bars) or price * meta["vol"]
            if direction == "LONG":
                take_profit = price + vol * (1.8 + risk)
                stop_loss = price - vol * (0.9 + 0.35 * risk)
            else:
                take_profit = price - vol * (1.8 + risk)
                stop_loss = price + vol * (0.9 + 0.35 * risk)

            rr = abs(take_profit - price) / max(abs(price - stop_loss), 1e-9)
            strategy = self._pick_strategy(direction, r, fast, slow, macd)
            reason = self._compose_reason(pair, direction, r, macd, fast, slow, confidence)

            sig_id = db_execute(
                """INSERT INTO signals
                   (pair, direction, entry, take_profit, stop_loss, timeframe,
                    confidence, strategy, reason, status, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'AI', ?)""",
                (
                    pair, direction, price, take_profit, stop_loss,
                    random.choice(TIMEFRAMES), confidence, strategy, reason, now_iso(),
                ),
            )
            return db_get("SELECT * FROM signals WHERE id = ?", (sig_id,))

    @staticmethod
    def _pick_strategy(direction, r, fast, slow, macd):
        if direction == "LONG" and r < 34:
            return "Oversold Reversal"
        if direction == "SHORT" and r > 66:
            return "Overbought Fade"
        if (direction == "LONG") == (fast > slow) and abs(macd) > 0:
            return "EMA Momentum"
        return "Breakout Continuation"

    @staticmethod
    def _compose_reason(pair, direction, r, macd, fast, slow, conf):
        bits = []
        if r < 34:
            bits.append(f"RSI(14) at {r:.1f} flags oversold pressure")
        elif r > 66:
            bits.append(f"RSI(14) at {r:.1f} flags overbought exhaustion")
        else:
            bits.append(f"RSI(14) balanced at {r:.1f}")
        trend = "above" if fast > slow else "below"
        bits.append(f"EMA-9 trading {trend} EMA-21")
        bits.append("MACD histogram expanding" if macd > 0 else "MACD histogram compressing")
        bits.append(f"ensemble agreement {conf:.0f}%")
        side = "long entry" if direction == "LONG" else "short entry"
        return f"{pair} {side}: " + "; ".join(bits) + "."

    # ----------------------------- queries ----------------------------
    def market_snapshot(self):
        with self.lock:
            out = []
            for pair, meta in PAIRS.items():
                bars = self.history[pair]
                first = bars[0]["c"] if bars else self.prices[pair]
                price = self.prices[pair]
                out.append(
                    {
                        "pair": pair,
                        "price": price,
                        "dp": meta["dp"],
                        "change_pct": (price - first) / first * 100 if first else 0.0,
                        "spark": [b["c"] for b in bars[-40:]],
                    }
                )
            return out

    def chart(self, pair):
        with self.lock:
            if pair not in self.history:
                return None
            bars = [dict(b) for b in self.history[pair][-110:]]
            return {"pair": pair, "dp": PAIRS[pair]["dp"], "bars": bars}

    def price(self, pair):
        with self.lock:
            return self.prices.get(pair)
