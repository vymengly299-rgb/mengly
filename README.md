# MENGLY — AI Trade Signal Dashboard

A self-contained trading-signal web app with an AI signal engine, live
(simulated) market charts, and role-based accounts where the **admin creates
users, and every user signs in with their own password**.

## Features

- **Authentication** — password login with hashed passwords (werkzeug),
  session-based, `admin` and `user` roles.
- **Admin panel** — create / disable / delete users, reset any user's
  password, place manual signals, cancel signals, tune the AI engine
  (auto-generation on/off, max concurrent signals, risk appetite).
- **AI signal engine** — background thread simulates live prices for 6
  markets (BTC, ETH, SOL, EUR/USD, GBP/USD, Gold), computes RSI / EMA / MACD
  over the series, and generates structured signals:
  `pair · direction · entry · take-profit · stop-loss · timeframe ·
  confidence · strategy · model rationale`.
- **Live dashboard** — candlestick chart, sparkline market cards, active
  signal tracker with SL→TP progress markers, win-rate & realized-R stats,
  AI activity feed. Auto-refreshes every 4 s. No external JS libraries.
- **Signal history** — filterable by status / pair / direction.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py        # http://0.0.0.0:5000
```

## Demo accounts

| Role   | Username | Password      |
| ------ | -------- | ------------- |
| Admin  | `admin`  | `Admin@123`   |
| Trader | `trader` | `Trader@123`  |

Admins create additional users from **Admin Panel → User Management**; each
new account is immediately usable with its assigned password.

## Notes

- Market data is **simulated** (random walk + indicator blend). Signals are
  demo content — **not financial advice**.
- State lives in a local SQLite file `app.db` (auto-created, git-ignored).
  Delete it to reset all users and signals.
