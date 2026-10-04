"""MENGLY — AI Trade Signal Dashboard (Flask)."""
import re
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import (db_execute, db_get, db_query, get_setting, init_db, now_iso,
                set_setting)
from engine import PAIRS, TIMEFRAMES, MarketEngine

app = Flask(__name__)
app.secret_key = "mengly-demo-secret-change-me"

init_db()

# --------------------------- seed accounts ----------------------------
if db_get("SELECT id FROM users WHERE username = 'admin'") is None:
    db_execute(
        "INSERT INTO users (username, password_hash, role, active, created_by, created_at) "
        "VALUES (?, ?, 'admin', 1, 'system', ?)",
        ("admin", generate_password_hash("Admin@123"), now_iso()),
    )
if db_get("SELECT id FROM users WHERE username = 'trader'") is None:
    db_execute(
        "INSERT INTO users (username, password_hash, role, active, created_by, created_at) "
        "VALUES (?, ?, 'user', 1, 'admin', ?)",
        ("trader", generate_password_hash("Trader@123"), now_iso()),
    )

engine = MarketEngine()
engine.start()
# warm up the signal board on first boot
if db_get("SELECT COUNT(*) AS n FROM signals")["n"] == 0:
    for _ in range(5):
        engine.generate_signal()


# ------------------------------ helpers -------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            flash("That area is restricted to administrators.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,20}$")


def fmt_dt(iso):
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %H:%M UTC")
    except ValueError:
        return iso


def time_ago(iso):
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    secs = max(0, (datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


@app.template_filter("dp")
def dp_filter(value, pair=None):
    digits = PAIRS.get(pair or "", {"dp": 2})["dp"] if pair else 2
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return value


app.jinja_env.filters["fmt_dt"] = fmt_dt
app.jinja_env.filters["time_ago"] = time_ago


@app.context_processor
def inject_globals():
    return {"ai_auto": get_setting("ai_auto", "1") == "1"}


def signal_stats():
    active = db_get("SELECT COUNT(*) AS n FROM signals WHERE status='ACTIVE'")["n"]
    wins = db_get("SELECT COUNT(*) AS n FROM signals WHERE status='TP_HIT'")["n"]
    losses = db_get("SELECT COUNT(*) AS n FROM signals WHERE status='SL_HIT'")["n"]
    closed = wins + losses
    win_rate = round(wins / closed * 100, 1) if closed else None
    avg_conf = db_get(
        "SELECT AVG(confidence) AS a FROM signals ORDER BY id DESC LIMIT 20"
    )["a"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_count = db_get(
        "SELECT COUNT(*) AS n FROM signals WHERE substr(created_at,1,10) = ?", (today,)
    )["n"]
    return {
        "active": active,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_conf": round(avg_conf, 1) if avg_conf else 0,
        "today": today_count,
        "realized_r": round(wins * 2 - losses, 1),
    }


def active_signals():
    rows = db_query(
        "SELECT * FROM signals WHERE status='ACTIVE' ORDER BY confidence DESC"
    )
    prices = {pair: engine.price(pair) for pair in PAIRS}
    for row in rows:
        price = prices.get(row["pair"]) or row["entry"]
        span = row["take_profit"] - row["stop_loss"]
        if abs(span) < 1e-12:
            pos = 50.0
        else:
            pos = (price - row["stop_loss"]) / span * 100
            if row["direction"] == "SHORT":
                pos = 100 - pos
        row["progress"] = max(0.0, min(100.0, pos))
        row["price"] = price
    return rows


def activity_feed(limit=10):
    rows = db_query(
        "SELECT * FROM signals ORDER BY COALESCE(closed_at, created_at) DESC LIMIT ?",
        (limit,),
    )
    return rows


# -------------------------------- auth --------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if "uid" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = db_get("SELECT * FROM users WHERE username = ?", (username,))
        if user and user["active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["uid"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash(f"Welcome back, {user['username']}.", "success")
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid credentials or deactivated account.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return redirect(url_for("dashboard") if "uid" in session else url_for("login"))


# ------------------------------- pages --------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        market=engine.market_snapshot(),
        stats=signal_stats(),
        signals=active_signals(),
        feed=activity_feed(),
        ai_auto=get_setting("ai_auto", "1") == "1",
        pair_list=list(PAIRS.keys()),
    )


@app.route("/signals")
@login_required
def signals_page():
    status = request.args.get("status", "")
    pair = request.args.get("pair", "")
    direction = request.args.get("direction", "")

    sql = "SELECT * FROM signals WHERE 1=1"
    args = []
    if status in ("ACTIVE", "TP_HIT", "SL_HIT", "CANCELLED"):
        sql += " AND status = ?"
        args.append(status)
    if pair in PAIRS:
        sql += " AND pair = ?"
        args.append(pair)
    if direction in ("LONG", "SHORT"):
        sql += " AND direction = ?"
        args.append(direction)
    sql += " ORDER BY COALESCE(closed_at, created_at) DESC LIMIT 200"
    rows = db_query(sql, tuple(args))
    prices = {p: engine.price(p) for p in PAIRS}
    for row in rows:
        row["price"] = prices.get(row["pair"])
    return render_template(
        "signals.html", signals=rows, stats=signal_stats(),
        filters={"status": status, "pair": pair, "direction": direction},
        pair_list=list(PAIRS.keys()), prices=prices,
    )


@app.route("/admin")
@admin_required
def admin():
    users = db_query("SELECT * FROM users ORDER BY id")
    actives = db_query("SELECT * FROM signals WHERE status='ACTIVE' ORDER BY id DESC")
    return render_template(
        "admin.html", users=users, signals=actives, stats=signal_stats(),
        pair_list=list(PAIRS.keys()), timeframes=TIMEFRAMES,
        settings={
            "ai_auto": get_setting("ai_auto", "1") == "1",
            "max_active": get_setting("max_active", "6"),
            "risk": get_setting("risk", "balanced"),
        },
    )


# ---------------------------- admin actions ---------------------------
@app.route("/admin/users", methods=["POST"])
@admin_required
def create_user():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    role = request.form.get("role") or "user"
    if role not in ("user", "admin"):
        role = "user"
    if not USERNAME_RE.match(username):
        flash("Username must be 3–20 chars: letters, numbers, dot or underscore.", "error")
    elif len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
    elif db_get("SELECT id FROM users WHERE username = ?", (username,)):
        flash(f"Username '{username}' is already taken.", "error")
    else:
        db_execute(
            "INSERT INTO users (username, password_hash, role, active, created_by, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (username, generate_password_hash(password), role, session["username"], now_iso()),
        )
        flash(f"User '{username}' created with password login.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:uid>/toggle", methods=["POST"])
@admin_required
def toggle_user(uid):
    user = db_get("SELECT * FROM users WHERE id = ?", (uid,))
    if not user:
        flash("User not found.", "error")
    elif user["id"] == session["uid"]:
        flash("You cannot deactivate your own account.", "error")
    else:
        db_execute("UPDATE users SET active = 1 - active WHERE id = ?", (uid,))
        state = "activated" if not user["active"] else "deactivated"
        flash(f"User '{user['username']}' {state}.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@admin_required
def delete_user(uid):
    user = db_get("SELECT * FROM users WHERE id = ?", (uid,))
    if not user:
        flash("User not found.", "error")
    elif user["id"] == session["uid"]:
        flash("You cannot delete your own account.", "error")
    else:
        db_execute("DELETE FROM users WHERE id = ?", (uid,))
        flash(f"User '{user['username']}' deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:uid>/reset-password", methods=["POST"])
@admin_required
def reset_password(uid):
    password = request.form.get("password") or ""
    user = db_get("SELECT * FROM users WHERE id = ?", (uid,))
    if not user:
        flash("User not found.", "error")
    elif len(password) < 6:
        flash("New password must be at least 6 characters.", "error")
    else:
        db_execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), uid),
        )
        flash(f"Password for '{user['username']}' has been reset.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/signals", methods=["POST"])
@admin_required
def create_signal():
    pair = request.form.get("pair")
    direction = request.form.get("direction")
    timeframe = request.form.get("timeframe") or "15m"
    if pair not in PAIRS or direction not in ("LONG", "SHORT") or timeframe not in TIMEFRAMES:
        flash("Invalid signal parameters.", "error")
        return redirect(url_for("admin"))

    price = engine.price(pair) or PAIRS[pair]["base"]

    def parse_num(name, fallback):
        raw = (request.form.get(name) or "").strip().replace(",", "")
        try:
            return float(raw) if raw else fallback
        except ValueError:
            return fallback

    digits = PAIRS[pair]["dp"]
    step = 10 ** -digits
    entry = parse_num("entry", price)
    if direction == "LONG":
        take_profit = parse_num("take_profit", entry + 60 * step)
        stop_loss = parse_num("stop_loss", entry - 30 * step)
    else:
        take_profit = parse_num("take_profit", entry - 60 * step)
        stop_loss = parse_num("stop_loss", entry + 30 * step)

    db_execute(
        """INSERT INTO signals
           (pair, direction, entry, take_profit, stop_loss, timeframe,
            confidence, strategy, reason, status, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'Manual Override', 'Manually placed by admin.', 'ACTIVE', 'MANUAL', ?)""",
        (pair, direction, entry, take_profit, stop_loss, timeframe, 100.0, now_iso()),
    )
    flash(f"Manual {direction} signal placed on {pair}.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/signals/<int:sid>/cancel", methods=["POST"])
@admin_required
def cancel_signal(sid):
    sig = db_get("SELECT * FROM signals WHERE id = ? AND status='ACTIVE'", (sid,))
    if sig:
        db_execute("UPDATE signals SET status='CANCELLED', closed_at=? WHERE id=?", (now_iso(), sid))
        flash(f"Signal #{sid} cancelled.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/settings", methods=["POST"])
@admin_required
def update_settings():
    set_setting("ai_auto", "1" if request.form.get("ai_auto") == "1" else "0")
    try:
        max_active = max(1, min(20, int(request.form.get("max_active") or 6)))
    except ValueError:
        max_active = 6
    risk = request.form.get("risk") or "balanced"
    if risk not in ("conservative", "balanced", "aggressive"):
        risk = "balanced"
    set_setting("max_active", max_active)
    set_setting("risk", risk)
    flash("AI engine settings saved.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/ai/generate", methods=["POST"])
@admin_required
def force_generate():
    sig = engine.generate_signal()
    if sig:
        flash(f"AI generated {sig['direction']} {sig['pair']} @ {sig['confidence']}% confidence.", "success")
    else:
        flash("Engine needs more price history — try again in a moment.", "error")
    return redirect(url_for("admin"))


# -------------------------------- APIs --------------------------------
@app.route("/api/market")
@login_required
def api_market():
    return jsonify({"market": engine.market_snapshot(),
                    "ai_auto": get_setting("ai_auto", "1") == "1"})


@app.route("/api/chart/<path:pair>")
@login_required
def api_chart(pair):
    data = engine.chart(pair)
    if not data:
        return jsonify({"error": "unknown pair"}), 404
    return jsonify(data)


@app.route("/api/signals")
@login_required
def api_signals():
    rows = active_signals()
    return jsonify({"signals": rows,
                    "prices": {p: engine.price(p) for p in PAIRS},
                    "stats": signal_stats()})


@app.route("/api/feed")
@login_required
def api_feed():
    return jsonify({"feed": activity_feed(12)})


# -------------------------------- main --------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
