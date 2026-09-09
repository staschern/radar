#!/usr/bin/env python3
"""Fetch full daily MOEX ISS history (board TQBR) for the 9 model-portfolio
tickers + TCSG (T-Технологии's predecessor ticker before its 2024
redomiciliation from Cyprus-registered TCS Group to Russian PJSC "T").

Retries on connection errors, saves incrementally so partial progress
survives a crash mid-fetch. Output feeds scripts/adjust_stock_splits.py —
these raw closes are NOT split-adjusted (see that script's docstring).
"""
import json, time, urllib.request, os

OUT = "data/quotes/daily_prices_9stocks_raw.json"

TICKERS = ["SBER", "LKOH", "SNGSP", "PLZL", "GMKN", "ROSN", "T", "TCSG", "MOEX", "YDEX"]

FROM = "2000-01-01"
TILL = "2026-12-31"


def fetch_url(url, retries=5):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"  retry {attempt+1}/{retries} after error: {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed after {retries} retries: {url}")


def fetch_ticker(secid):
    rows = []
    start = 0
    while True:
        url = (f"https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/"
               f"securities/{secid}.json?from={FROM}&till={TILL}&start={start}")
        data = fetch_url(url)
        hist = data["history"]
        cols = hist["columns"]
        di = cols.index("TRADEDATE")
        ci = cols.index("CLOSE")
        page_rows = [(r[di], r[ci]) for r in hist["data"] if r[ci] is not None]
        rows.extend(page_rows)
        n = len(hist["data"])
        print(f"  {secid} start={start} got={n} total={len(rows)}", flush=True)
        if n < 100:
            break
        start += 100
    return rows


def main():
    results = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            results = json.load(f)
        print(f"resuming, already have: {list(results.keys())}", flush=True)

    for t in TICKERS:
        if t in results and len(results[t]) > 0:
            print(f"skip {t}, already have {len(results[t])} rows", flush=True)
            continue
        print(f"fetching {t}...", flush=True)
        try:
            rows = fetch_ticker(t)
            results[t] = rows
            with open(OUT, "w") as f:
                json.dump(results, f)
            print(f"saved {t}: {len(rows)} rows, {rows[0][0] if rows else '-'} .. {rows[-1][0] if rows else '-'}", flush=True)
        except Exception as e:
            print(f"FAILED {t}: {e}", flush=True)

    print("DONE ALL", flush=True)


if __name__ == "__main__":
    main()
