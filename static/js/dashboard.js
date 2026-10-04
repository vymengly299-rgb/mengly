/* MENGLY dashboard: live polling + canvas charts (no external libs). */
(function () {
  "use strict";

  var state = {
    selected: (window.__INITIAL__ && window.__INITIAL__.selected) || "BTC/USDT",
    dp: {},
    market: [],
    dirty: true,
  };

  (window.__INITIAL__ ? window.__INITIAL__.market : []).forEach(function (m) {
    state.dp[m.pair] = m.dp;
  });

  function dp(pair) { return state.dp[pair] != null ? state.dp[pair] : 2; }
  function fmt(pair, v) {
    if (v == null) return "—";
    return Number(v).toLocaleString("en-US", {
      minimumFractionDigits: dp(pair), maximumFractionDigits: dp(pair),
    });
  }
  function $(id) { return document.getElementById(id); }

  function sizeCanvas(canvas) {
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    var w = Math.max(rect.width, 60);
    var h = Math.max(rect.height, 30);
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: w, h: h };
  }

  /* ---------------- sparklines ---------------- */
  function drawSpark(canvas, data, up) {
    if (!canvas || !data || data.length < 2) return;
    var s = sizeCanvas(canvas), ctx = s.ctx, w = s.w, h = s.h;
    ctx.clearRect(0, 0, w, h);
    var min = Math.min.apply(null, data), max = Math.max.apply(null, data);
    var pad = (max - min) || 1;
    min -= pad * 0.1; max += pad * 0.1;
    var color = up ? "#2fd273" : "#ff5d6c";
    ctx.beginPath();
    data.forEach(function (v, i) {
      var x = (i / (data.length - 1)) * w;
      var y = h - ((v - min) / (max - min)) * h;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    var grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, up ? "rgba(47,210,115,0.25)" : "rgba(255,93,108,0.25)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad; ctx.fill();
  }

  /* ---------------- candlestick chart ---------------- */
  function drawCandles(canvas, bars, pair) {
    if (!canvas || !bars || !bars.length) return;
    var s = sizeCanvas(canvas), ctx = s.ctx, w = s.w, h = s.h;
    ctx.clearRect(0, 0, w, h);
    var padR = 62, padY = 12;
    var min = Infinity, max = -Infinity;
    bars.forEach(function (b) { min = Math.min(min, b.l); max = Math.max(max, b.h); });
    var span = (max - min) || 1;
    min -= span * 0.05; max += span * 0.05;
    span = max - min;

    function y(v) { return padY + (1 - (v - min) / span) * (h - padY * 2); }

    // gridlines + price labels
    ctx.font = "10px ui-monospace, Menlo, monospace";
    for (var g = 0; g <= 4; g++) {
      var val = min + (span * g) / 4, yy = y(val);
      ctx.strokeStyle = "rgba(255,255,255,0.05)";
      ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
      ctx.fillStyle = "#8b98ad";
      ctx.fillText(fmt(pair, val), w - padR + 8, yy + 3);
    }

    var cw = (w - padR) / bars.length;
    var bodyW = Math.max(2, cw * 0.6);
    bars.forEach(function (b, i) {
      var x = i * cw + cw / 2;
      var up = b.c >= b.o;
      var color = up ? "#2fd273" : "#ff5d6c";
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, y(b.h)); ctx.lineTo(x, y(b.l)); ctx.stroke();
      var top = y(Math.max(b.o, b.c)), bh = Math.max(Math.abs(y(b.o) - y(b.c)), 1.2);
      ctx.globalAlpha = up ? 0.95 : 0.9;
      ctx.fillRect(x - bodyW / 2, top, bodyW, bh);
      ctx.globalAlpha = 1;
    });
  }

  /* ---------------- pair cards ---------------- */
  function renderMarket(market) {
    state.market = market;
    market.forEach(function (m) { state.dp[m.pair] = m.dp; });
    var cards = document.querySelectorAll(".pair-card");
    cards.forEach(function (card) {
      var pair = card.getAttribute("data-pair");
      var m = market.find(function (x) { return x.pair === pair; });
      if (!m) return;
      card.querySelector("[data-role=price]").textContent = fmt(pair, m.price);
      var chg = card.querySelector(".pair-chg");
      chg.textContent = (m.change_pct >= 0 ? "+" : "") + m.change_pct.toFixed(2) + "%";
      chg.className = "pair-chg " + (m.change_pct >= 0 ? "up" : "down");
      drawSpark(card.querySelector("[data-role=spark]"), m.spark, m.change_pct >= 0);
    });
  }

  function selectPair(pair) {
    state.selected = pair;
    document.querySelectorAll(".pair-card").forEach(function (c) {
      c.classList.toggle("selected", c.getAttribute("data-pair") === pair);
    });
    $("chart-title").textContent = pair;
    state.dirty = true;
    refreshChart();
  }

  document.querySelectorAll(".pair-card").forEach(function (card) {
    card.addEventListener("click", function () {
      selectPair(card.getAttribute("data-pair"));
    });
  });

  /* ---------------- chart refresh ---------------- */
  function refreshChart() {
    fetch("/api/chart/" + encodeURIComponent(state.selected))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.bars) return;
        drawCandles($("candles"), data.bars, data.pair);
        var last = data.bars[data.bars.length - 1];
        var priceEl = $("chart-price");
        if (priceEl && last) {
          priceEl.textContent = fmt(data.pair, last.c);
          priceEl.className = "chart-price " + (last.c >= last.o ? "green" : "red");
        }
      })
      .catch(function () {});
  }

  /* ---------------- active signals table ---------------- */
  function badge(side) {
    return '<span class="badge ' + (side === "LONG" ? "long" : "short") + '">' + side + "</span>";
  }
  function esc(s) {
    var d = document.createElement("div"); d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function renderSignals(payload) {
    var stats = payload.stats || {};
    $("stat-conf").textContent = (stats.avg_conf || 0) + "%";
    $("stat-active").textContent = stats.active != null ? stats.active : "—";
    var wr = $("stat-winrate");
    wr.textContent = stats.win_rate != null ? stats.win_rate + "%" : "—";
    var rEl = $("stat-r");
    var r = stats.realized_r || 0;
    rEl.textContent = (r >= 0 ? "+" : "") + r + "R";
    rEl.className = "stat-value " + (r >= 0 ? "green" : "red");

    var tbody = document.querySelector("#active-table tbody");
    if (!tbody) return;
    var rows = payload.signals || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="10" class="empty">No active signals — the AI engine is scanning the market…</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(function (s) {
      return "<tr>" +
        '<td class="muted">' + s.id + "</td>" +
        '<td class="mono">' + esc(s.pair) + "</td>" +
        "<td>" + badge(s.direction) + "</td>" +
        '<td class="mono">' + fmt(s.pair, s.entry) + "</td>" +
        '<td class="mono green">' + fmt(s.pair, s.take_profit) + "</td>" +
        '<td class="mono red">' + fmt(s.pair, s.stop_loss) + "</td>" +
        "<td>" + esc(s.timeframe) + "</td>" +
        "<td>" + esc(s.strategy) + "</td>" +
        '<td><div class="conf-cell"><div class="conf-bar"><span style="width:' + s.confidence + '%"></span></div><small>' + s.confidence + "%</small></div></td>" +
        '<td><div class="prog"><div class="prog-track"><span class="prog-marker" style="left:' + s.progress.toFixed(1) + '%"></span></div>' +
        '<div class="prog-labels"><small>SL</small><small>TP</small></div></div></td>' +
        "</tr>";
    }).join("");
  }

  function timeAgo(iso) {
    if (!iso) return "—";
    var t = Date.parse(iso);
    if (isNaN(t)) return iso;
    var secs = Math.max(0, (Date.now() - t) / 1000);
    if (secs < 60) return Math.floor(secs) + "s ago";
    if (secs < 3600) return Math.floor(secs / 60) + "m ago";
    if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
    return Math.floor(secs / 86400) + "d ago";
  }

  /* ---------------- feed ---------------- */
  function feedHTML(row) {
    var dot = row.status === "TP_HIT" ? "green" : row.status === "SL_HIT" ? "red" : "cyan";
    var body;
    if (row.status === "ACTIVE") {
      var side = '<em class="' + (row.direction === "LONG" ? "long" : "short") + '">' + row.direction + "</em>";
      body = "<strong>" + esc(row.strategy) + "</strong> flagged " + side + " " + esc(row.pair) +
             " @ " + row.confidence + "%";
    } else if (row.status === "TP_HIT") {
      body = "<strong>Take-profit hit</strong> on " + esc(row.direction) + " " + esc(row.pair);
    } else if (row.status === "SL_HIT") {
      body = "<strong>Stop-loss hit</strong> on " + esc(row.direction) + " " + esc(row.pair);
    } else {
      body = "<strong>Signal cancelled</strong> — " + esc(row.pair);
    }
    return '<li class="feed-item"><span class="feed-dot ' + dot + '"></span><div class="feed-body">' +
           body + '<span class="feed-time">' + timeAgo(row.closed_at || row.created_at) + "</span></div></li>";
  }

  function refreshFeed() {
    fetch("/api/feed").then(function (r) { return r.json(); }).then(function (data) {
      var feed = $("feed");
      if (feed && data.feed) feed.innerHTML = data.feed.map(feedHTML).join("");
    }).catch(function () {});
  }

  /* ---------------- clock ---------------- */
  function tickClock() {
    var el = $("clock");
    if (el) el.textContent = new Date().toISOString().slice(11, 19) + " UTC";
  }

  /* ---------------- polling loop ---------------- */
  function poll() {
    fetch("/api/market").then(function (r) { return r.json(); }).then(renderMarket).catch(function () {});
    fetch("/api/signals").then(function (r) { return r.json(); }).then(renderSignals).catch(function () {});
    refreshChart();
  }

  tickClock();
  setInterval(tickClock, 1000);
  renderMarket(state.market.length ? state.market : (window.__INITIAL__ ? window.__INITIAL__.market : []));
  selectPair(state.selected);
  refreshFeed();
  poll();
  setInterval(poll, 4000);
  setInterval(refreshFeed, 12000);
  window.addEventListener("resize", function () {
    renderMarket(state.market);
    refreshChart();
  });
})();
