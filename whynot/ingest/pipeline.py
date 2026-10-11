"""Downloads, in the order a fresh machine needs them, and the availability report.
Every download resumes from what is already stored and records a run. A bar whose
period has not finished yet is never stored."""

import csv
import io
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import validate
from .alpaca import DATA, AlpacaError
from .timeutil import ET, daily_available_at, epoch, et_day, et_time, iso, next_weekday, parse_ts

SYMBOLS = ("SPY", "QQQ", "IWM")
ALPACA_START = "2016-01-01"                 # where this account's stock history begins
# Index and volatility series that stand in for the ETFs before they existed.
YAHOO = [("^GSPC", "LONG_TERM_MARKET_HISTORY", "SPY"), ("^NDX", "LONG_TERM_MARKET_HISTORY", "QQQ"),
         ("^RUT", "LONG_TERM_MARKET_HISTORY", "IWM"), ("^VIX", "LONG_TERM_MARKET_HISTORY", None),
         ("SPY", "ETF_HISTORY", None), ("QQQ", "ETF_HISTORY", None), ("IWM", "ETF_HISTORY", None)]
FRED = ["DGS10", "DGS2", "DFF"]             # market rates: published once, not revised


def _row(bar: dict, available_at: int) -> tuple:
    return (parse_ts(bar["t"]), bar["o"], bar["h"], bar["l"], bar["c"], bar.get("v"), bar.get("vw"), bar.get("n"), available_at)


def _run(cat, ds, start, end, fetch) -> int:
    """Fetch everything, then store it in one go, so a failure leaves nothing partial."""
    run = cat.start_run(ds, start, end)
    try:
        added = cat.add_bars(ds, fetch())
    except Exception as err:
        cat.finish_run(run, 0, "failed", str(err)[:300])
        raise
    cat.finish_run(run, added, "ok")
    return added


def ingest_calendar(cat, client, start="1970-01-01", end=None) -> int:
    end = end or (date.today() + timedelta(days=370)).isoformat()
    rows = [(d["date"], d["open"], d["close"]) for d in client.calendar(start, end)]
    cat.set_calendar(rows, source="alpaca")
    return len(rows)


def ingest_actions(cat, client, start=ALPACA_START) -> int:
    rows = []
    for kind, item in client.corporate_actions(list(SYMBOLS), start, date.today().isoformat()):
        if kind == "cash_dividends":
            rows.append((item["symbol"], item["ex_date"], "cash_dividend", item.get("rate")))
        else:
            rows.append((item["symbol"], item["ex_date"], kind.rstrip("s"), item.get("new_rate", 1) / (item.get("old_rate", 1) or 1)))
    cat.add_actions(rows, source="alpaca")
    return len(rows)


def ingest_daily(cat, client, symbol, start=ALPACA_START, end=None, now=None) -> int:
    """Unadjusted daily candles from Alpaca. Returns the dataset id."""
    now = now or time.time()
    ds = cat.dataset(category="ETF_HISTORY", symbol=symbol, timeframe="1Day", source="alpaca", authentic=True, adjustment="raw")
    last = cat.coverage(ds)["last"]
    begin = et_day(last).isoformat() if last else start

    def fetch():
        rows = []
        for bar in client.stock_bars(symbol, "1Day", begin, f"{end}T23:59:59Z" if end else None):
            ts = parse_ts(bar["t"])
            available = daily_available_at(cat, et_day(ts))
            if available <= now:                                  # today's candle is not a candle until the close
                rows.append(_row(bar, available))
        return rows

    _run(cat, ds, begin, end or "now", fetch)
    return ds


def ingest_minutes(cat, client, symbol, start=ALPACA_START, now=None, log=print) -> int:
    """Unadjusted 1-minute candles, a month at a time. A minute is known when it ends."""
    now = now or time.time()
    ds = cat.dataset(category="ETF_HISTORY", symbol=symbol, timeframe="1Min", source="alpaca", authentic=True, adjustment="raw")
    last = cat.coverage(ds)["last"]
    month = (et_day(last) if last else date.fromisoformat(start)).replace(day=1)
    today = datetime.now(ET).date()
    while month <= today:
        following = (month + timedelta(days=32)).replace(day=1)
        begin = max(month, et_day(last)) if last else month
        end = None if following > today else f"{following.isoformat()}T00:00:00Z"
        fetch = lambda: [_row(b, parse_ts(b["t"]) + 60) for b in client.stock_bars(symbol, "1Min", begin.isoformat(), end)
                         if parse_ts(b["t"]) + 60 <= now]
        added = _run(cat, ds, begin.isoformat(), end or "now", fetch)
        log(f"  {symbol} 1Min {month:%Y-%m}: {added:,} new bars")
        month = following
    return ds


def ingest_yahoo(cat, ticker, category, proxy_for=None, now=None) -> int:
    """Daily history from Yahoo, back to the start of the series. Yahoo's prices are
    adjusted for splits but not dividends, and are labeled so."""
    import yfinance as yf

    now = now or time.time()
    ds = cat.dataset(category=category, symbol=ticker, timeframe="1Day", source="yahoo", authentic=True,
                     adjustment="split_adjusted", proxy_for=proxy_for,
                     note="index, not tradable" if ticker.startswith("^") else "free feed; cross-checked against Alpaca from 2016")

    def fetch():
        df = yf.download(ticker, period="max", interval="1d", auto_adjust=False, progress=False, multi_level_index=False)
        rows = []
        for day, r in df.dropna(subset=["Close"]).iterrows():
            d = day.date()
            ts, available = epoch(datetime(d.year, d.month, d.day, tzinfo=ET)), daily_available_at(cat, d)
            if available <= now:
                rows.append((ts, float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]), float(r["Volume"]), None, None, available))
        return rows

    _run(cat, ds, "max", "now", fetch)
    return ds


def ingest_fred(cat, series, now=None) -> int:
    """A market-rate series from FRED. The value for a day is treated as known at 18:00
    New York time on the next weekday. Not for revised series such as CPI or payrolls."""
    now = now or time.time()
    ds = cat.dataset(category="LONG_TERM_MARKET_HISTORY", symbol=series, timeframe="1Day", source="fred", authentic=True,
                     adjustment="none", note="market rate, not revised; availability assumed next weekday 18:00 ET")

    def fetch():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8.4.0"}), timeout=30) as resp:
                    reader = csv.reader(io.StringIO(resp.read().decode()))
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(3)
        next(reader)
        rows = []
        for day, value in reader:
            try:
                v, d = float(value), date.fromisoformat(day)
            except ValueError:
                continue                                          # FRED marks a missing day with "."
            ts, available = epoch(datetime(d.year, d.month, d.day, tzinfo=ET)), et_time(next_weekday(d), "18:00")
            if available <= now:
                rows.append((ts, v, v, v, v, None, None, None, available))
        return rows

    _run(cat, ds, "max", "now", fetch)
    return ds


def ingest_contracts(cat, client, underlying, start="2024-01-01", log=print) -> int:
    """Every listed contract, expired and live, a month of expirations at a time."""
    total, month, today = 0, date.fromisoformat(start).replace(day=1), date.today()
    while month <= today + timedelta(days=400):
        following = (month + timedelta(days=32)).replace(day=1)
        last_day = (following - timedelta(days=1)).isoformat()
        rows = []
        for status in ("inactive", "active"):
            for c in client.option_contracts(underlying, status, month.isoformat(), last_day):
                rows.append((c["symbol"], underlying, c["type"], float(c["strike_price"]), c["expiration_date"], c["status"]))
        cat.add_contracts(rows, source="alpaca")
        total += len(rows)
        if rows:
            log(f"  {underlying} contracts expiring {month:%Y-%m}: {len(rows):,}")
        month = following
    return total


def ingest_option_bars(cat, client, contracts, start, end, timeframe="1Day", now=None) -> int:
    """Real traded bars for the given contracts. A contract with no trades gets no rows."""
    now, added = now or time.time(), 0
    # Alpaca refuses a request that reaches into the last 15 minutes without a paid data agreement, so a
    # range ending today is sent with no end at all and the provider stops where this account may see.
    last = None if end >= datetime.now(timezone.utc).date().isoformat() else f"{end}T23:59:59Z"
    for i in range(0, len(contracts), 100):
        by_contract = {}
        for contract, bar in client.option_bars(contracts[i:i + 100], timeframe, start, last):
            ts = parse_ts(bar["t"])
            available = daily_available_at(cat, et_day(ts)) if timeframe == "1Day" else ts + 60
            if available <= now:
                by_contract.setdefault(contract, []).append(_row(bar, available))
        for contract, rows in by_contract.items():
            added += cat.add_option_bars(contract, timeframe, rows, source="alpaca")
    return added


def ingest_option_sample(cat, client, underlying, band=0.05, days_before=45, log=print) -> int:
    """Daily bars for the monthly (third-Friday) expirations, strikes within the band of
    the underlying's close when the window opens. A bounded first slice of real option
    history; the options milestone decides what else it needs."""
    daily = cat.find(underlying, "1Day", "alpaca")
    closes = {et_day(b["ts"]): b["close"] for b in cat.bars(daily)} if daily else {}
    expirations = sorted({c["expiration"] for c in cat.contracts(underlying)})
    added = 0
    for exp in expirations:
        d = date.fromisoformat(exp)
        if not (d.weekday() == 4 and 15 <= d.day <= 21) or d > date.today():
            continue
        opens = d - timedelta(days=days_before)
        ref = next((closes[x] for x in sorted(closes) if x >= opens), None)
        if ref is None:
            continue
        near = [c["symbol"] for c in cat.contracts(underlying, exp, exp) if abs(c["strike"] / ref - 1) <= band]
        got = ingest_option_bars(cat, client, near, opens.isoformat(), exp)
        added += got
        log(f"  {underlying} options expiring {exp}: {len(near)} contracts near the money, {got:,} daily bars")
    return added


def probe(cat, client) -> None:
    """What this account can and cannot reach, recorded as findings for the report."""
    quotes = client.status(f"{DATA}/v1beta1/options/quotes", {"symbols": "SPY240315C00500000", "start": "2024-03-04T00:00:00Z", "limit": 1})
    cat.add_finding("option quotes", "Historical option bid/ask quotes: available." if quotes == "ok" else
                    "Historical option bid/ask quotes are NOT available from Alpaca (the request is refused). Only traded "
                    "prices exist, so historical spreads, implied volatility and Greeks cannot be read; they would have to be "
                    "derived from trades and labeled as derived.")
    for label, start in (("before 2024", "2023-06-01"), ("from 2024", "2024-02-05")):
        try:
            exp = "230616" if label == "before 2024" else "240223"
            n = sum(1 for _ in client.option_bars([f"SPY{exp}C00430000"], "1Day", start, start[:8] + "28T00:00:00Z"))
        except AlpacaError as err:
            n = f"refused ({err})"
        cat.add_finding(f"option bars {label}", f"A liquid SPY call checked {label}: {n} daily bars returned.")
    for feed in ("sip", "iex"):
        try:
            first = next(iter(client.stock_bars("SPY", "1Day", "1970-01-01", "1971-01-01T00:00:00Z", feed)), None)
            first = first or next(iter(client.stock_bars("SPY", "1Day", "1970-01-01", None, feed)), None)
            text = first["t"][:10] if first else "nothing"
        except AlpacaError as err:
            text = f"refused ({err})"
        cat.add_finding(f"stock history ({feed} feed)", f"Earliest SPY daily bar on the {feed} feed: {text}.")


def validate_all(cat, log=print) -> None:
    for d in cat.datasets():
        ds, cov = d["id"], cat.coverage(d["id"])
        if not cov["rows"]:
            continue
        first, last = et_day(cov["first"]).isoformat(), et_day(cov["last"]).isoformat()
        bad = validate.ohlc(cat.iter_bars(ds)) if d["source"] != "fred" else []
        cat.add_quality(ds, "ohlc", not bad, f"{len(bad):,} impossible bars" + (f", first at {iso(bad[0]['ts'])}" if bad else ""))
        gaps = []
        if d["source"] != "fred":
            sessions = cat.sessions(first, last)
            if d["timeframe"] == "1Day":
                gaps = validate.daily_calendar([et_day(r[0]) for r in cat.iter_bars(ds)], sessions)
            elif d["timeframe"] == "1Min":
                gaps = validate.minutes([r[0] for r in cat.iter_bars(ds)], sessions)
        cat.add_gaps(ds, gaps)
        if d["timeframe"] == "1Day" and d["source"] != "fred" and d["symbol"] != "^VIX":   # a 20% day is ordinary for the VIX
            splits = {a["ex_date"]: a["value"] for a in cat.actions(d["symbol"]) if "split" in a["kind"]}
            jumps = validate.outliers(cat.iter_bars(ds), splits)
            cat.add_quality(ds, "unexplained jumps over 20%", not jumps, ", ".join(f"{j['day']} {j['ret']:+.1%}" for j in jumps[:12]))
        log(f"  checked {d['symbol']} {d['timeframe']} ({d['source']}): {len(bad)} bad bars, {len(gaps)} gaps")
    for symbol in SYMBOLS:                                         # two sources for the same ETF should agree
        a, b = cat.find(symbol, "1Day", "alpaca"), cat.find(symbol, "1Day", "yahoo")
        if a and b:
            closes = lambda ds: {et_day(r[0]).isoformat(): r[4] for r in cat.iter_bars(ds)}
            diff = validate.cross_source(closes(a), closes(b))
            cat.add_quality(a, "agrees with Yahoo within 0.5%", not diff,
                            f"{len(diff)} days differ" + (": " + ", ".join(f"{x['day']} {x['a']} vs {x['b']}" for x in diff[:8]) if diff else ""))


def report(cat) -> str:
    out = ["# Data availability report", "",
           f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from the catalog. Every figure below is a count of what is stored.", ""]
    by_category = {}
    for d in cat.datasets():
        by_category.setdefault(d["category"], []).append(d)
    for category in ("ETF_HISTORY", "LONG_TERM_MARKET_HISTORY", "OPTIONS_HISTORY", "LIVE_MARKET_DATA"):
        out += [f"## {category}", ""]
        if category == "OPTIONS_HISTORY":
            out += _options(cat)
            continue
        rows = by_category.get(category, [])
        if not rows:
            out += ["Nothing stored yet." if category != "LIVE_MARKET_DATA" else "Nothing yet. This fills once paper trading starts.", ""]
            continue
        out += ["| Series | Candles | Source | Kind | Prices | First | Last | Rows | Gaps |", "|---|---|---|---|---|---|---|---|---|"]
        for d in rows:
            cov = cat.coverage(d["id"])
            kind = f"proxy for {d['proxy_for']}" if d["proxy_for"] else "authentic"
            out.append(f"| {d['symbol']} | {d['timeframe']} | {d['source']} | {kind} | {d['adjustment']} | {iso(cov['first'])} | "
                       f"{iso(cov['last'])} | {cov['rows']:,} | {len(cat.gaps(d['id'])):,} |")
        out.append("")
    out += ["## Checks", ""]
    for d in cat.datasets():
        for q in cat.quality(d["id"]):
            out.append(f"- {d['symbol']} {d['timeframe']} ({d['source']}): {q['name']}: {'pass' if q['passed'] else 'FLAGGED'}"
                       + (f" ({q['detail']})" if q["detail"] and not q["passed"] else ""))
        revised = cat.revisions(d["id"])
        if revised:
            out.append(f"- {d['symbol']} {d['timeframe']} ({d['source']}): {len(revised)} bars were later changed by the provider; the first value was kept.")
    out += ["", "## Gaps", ""]
    any_gap = False
    for d in cat.datasets():
        gaps = cat.gaps(d["id"])
        if not gaps:
            continue
        any_gap = True
        kinds = {}
        for g in gaps:
            kinds.setdefault(g["kind"], []).append(g)
        for kind, items in kinds.items():
            shown = ", ".join(g["day"] + (f" ({g['found']}/{g['expected']})" if g["expected"] else "") for g in items[:10])
            out.append(f"- {d['symbol']} {d['timeframe']} ({d['source']}): {len(items):,} x {kind}: {shown}{' ...' if len(items) > 10 else ''}")
    if not any_gap:
        out.append("None found.")
    out += ["", "## What the account can and cannot reach", ""]
    out += [f"- {f['text']}" for f in cat.findings()] or ["Not probed yet."]
    return "\n".join(out) + "\n"


def _options(cat) -> list:
    out = ["| Underlying | Contracts listed | Expirations | Contracts with stored bars | Daily bars stored | First bar | Last bar |", "|---|---|---|---|---|---|---|"]
    for symbol in SYMBOLS:
        n, lo, hi = cat.db.execute("SELECT COUNT(*), MIN(expiration), MAX(expiration) FROM contract WHERE underlying=?", (symbol,)).fetchone()
        traded, bars, first, last = cat.db.execute(
            "SELECT COUNT(DISTINCT contract), COUNT(*), MIN(ts), MAX(ts) FROM option_bar WHERE contract >= ? AND contract < ?",
            (symbol, symbol + "~")).fetchone()
        out.append(f"| {symbol} | {n:,} | {lo or '-'} to {hi or '-'} | {traded:,} | {bars:,} | {iso(first)} | {iso(last)} |")
    return out + ["", "Option rows are real traded bars from Alpaca. No price, spread or Greek here is modeled.", ""]


def _optional(log, fn, *args):
    """A free secondary source being down must not stop the Alpaca downloads. The failed
    run is already on record; the report shows the series as empty."""
    try:
        fn(*args)
    except Exception as err:
        log(f"  skipped: {type(err).__name__}: {str(err)[:120]}")


def ingest_all(cat, client, minutes=True, options=True, log=print) -> None:
    log("calendar"); ingest_calendar(cat, client)
    log("corporate actions"); ingest_actions(cat, client)
    probe(cat, client)
    for symbol in SYMBOLS:
        log(f"{symbol} daily (Alpaca)"); ingest_daily(cat, client, symbol)
    for ticker, category, proxy_for in YAHOO:
        log(f"{ticker} daily (Yahoo)"); _optional(log, ingest_yahoo, cat, ticker, category, proxy_for)
    for series in FRED:
        log(f"{series} (FRED)"); _optional(log, ingest_fred, cat, series)
    if options:
        for symbol in SYMBOLS:
            log(f"{symbol} option contracts"); ingest_contracts(cat, client, symbol, log=log)
            ingest_option_sample(cat, client, symbol, log=log)
    if minutes:
        for symbol in SYMBOLS:
            log(f"{symbol} minutes (Alpaca)"); ingest_minutes(cat, client, symbol, log=log)
    log("validating"); validate_all(cat, log=log)
