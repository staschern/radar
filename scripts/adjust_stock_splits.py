#!/usr/bin/env python3
"""Калибрует сырые (не скорректированные на сплиты) дневные цены MOEX ISS
против известно-верной скорректированной МЕСЯЧНОЙ серии data/quotes/prices.json.

Зачем: сырой CLOSE из iss.moex.com/.../history не корректируется на сплиты.
Подтверждено на GMKN (сплит 1:100, 08.04.2024), PLZL (1:10, 27.03.2025) и
T (1:10, 17.04.2026) — без коррекции доходность позиции (price/avg_cost-1),
на которой строится правило §10 фиксации/откупа, была бы искажена в сотни
раз в месяц сплита.

Для каждого тикера:
  1. Считаем scale(month) = adjusted_monthly[month] / raw_close_at_month_end
  2. Ищем месяцы, где scale скачет (сплит внутри месяца)
  3. Внутри такого месяца ищем точный день сплита (наибольший скачок цены,
     совпадающий по модулю с обнаруженным коэффициентом)
  4. Строим дневной множитель adj_factor(day) и сохраняем adjusted-серию

Также склеивает T = TCSG (до редомициляции в 2024) + T (после).
"""
import json, math, bisect

RAW = "data/quotes/daily_prices_9stocks_raw.json"
OUT = "data/quotes/daily_prices_9stocks_adjusted.json"
MONTHLY_GT = "data/quotes/prices.json"

TICKERS = ["SBER", "LKOH", "SNGSP", "PLZL", "GMKN", "ROSN", "T", "MOEX", "YDEX"]
SPLIT_THRESH = math.log(1.4)  # >40% jump in scale => treat as split boundary


def splice_t(raw):
    """T = TCSG (pre-redomiciliation) + T (post), stitched at the boundary with a
    continuity ratio (T's first close / TCSG's last close before it) removed so the
    series is continuous in price level; actual split-adjustment vs monthly GT
    still happens afterward like any other ticker."""
    tcsg = sorted(raw.get("TCSG", []), key=lambda r: r[0])
    t = sorted(raw.get("T", []), key=lambda r: r[0])
    if not tcsg or not t:
        return t or tcsg
    cutover = t[0][0]
    tcsg_before = [r for r in tcsg if r[0] < cutover]
    if not tcsg_before:
        return t
    last_tcsg_price = tcsg_before[-1][1]
    first_t_price = t[0][1]
    ratio = first_t_price / last_tcsg_price if last_tcsg_price else 1.0
    print(f"  T splice: TCSG last={last_tcsg_price} @ {tcsg_before[-1][0]}  ->  T first={first_t_price} @ {cutover}  ratio={ratio:.4f}")
    tcsg_rescaled = [[d, p * ratio] for d, p in tcsg_before]
    return tcsg_rescaled + t


def month_end_value(rows_sorted, month):
    """rows_sorted: list of [date,price] sorted asc. Returns last close on/before month end."""
    candidates = [r for r in rows_sorted if r[0][:7] == month]
    if not candidates:
        return None
    return candidates[-1][1], candidates[-1][0]


def compute_scales(rows_sorted, monthly_gt):
    months = sorted(set(d[:7] for d, _ in rows_sorted) & set(monthly_gt.keys()))
    scales = []
    for m in months:
        mv = month_end_value(rows_sorted, m)
        if mv is None:
            continue
        raw_price, raw_date = mv
        if raw_price <= 0:
            continue
        gt = monthly_gt[m]
        scales.append((m, raw_date, gt / raw_price))
    return scales


def find_split_boundaries(scales):
    boundaries = []
    for i in range(1, len(scales)):
        m_prev, _, s_prev = scales[i-1]
        m_cur, d_cur, s_cur = scales[i]
        if s_prev <= 0 or s_cur <= 0:
            continue
        logratio = math.log(s_cur / s_prev)
        if abs(logratio) > SPLIT_THRESH:
            ratio = s_cur / s_prev  # scale jump factor (raw price effectively divided by ~ratio)
            boundaries.append((m_prev, m_cur, ratio))
    return boundaries


def locate_split_day(rows_sorted, month_prev, month_cur, expected_ratio):
    """Search raw daily closes across month_prev+month_cur for the exact day the
    split occurred: the single day-over-day ratio closest (in log space) to
    1/expected_ratio (since expected_ratio = scale_after/scale_before, and
    raw_price_after/raw_price_before = scale_before/scale_after = 1/expected_ratio)."""
    window = [r for r in rows_sorted if r[0][:7] in (month_prev, month_cur)]
    window.sort(key=lambda r: r[0])
    target_log = math.log(1.0 / expected_ratio)
    best = None
    for i in range(1, len(window)):
        p0, p1 = window[i-1][1], window[i][1]
        if p0 <= 0 or p1 <= 0:
            continue
        lr = math.log(p1 / p0)
        diff = abs(lr - target_log)
        if best is None or diff < best[0]:
            best = (diff, window[i][0], p1 / p0)
    return best  # (diff, split_date, actual_ratio) or None


def adjust_series(ticker, rows):
    rows_sorted = sorted(rows, key=lambda r: r[0])
    monthly_gt_all = json.load(open(MONTHLY_GT))
    gt = monthly_gt_all.get(ticker, {})
    if not gt:
        print(f"  {ticker}: no monthly ground truth, skipping adjustment (using raw as-is)")
        return rows_sorted

    scales = compute_scales(rows_sorted, gt)
    boundaries = find_split_boundaries(scales)
    if not boundaries:
        print(f"  {ticker}: no split detected across {len(scales)} calibration months")
        # still apply a constant scale = average of last few months to fix any
        # residual currency/lot-size mismatch between raw and GT bases
        avg_scale = scales[-1][2] if scales else 1.0
        return [[d, p * avg_scale] for d, p in rows_sorted]

    print(f"  {ticker}: {len(boundaries)} split boundary(ies) detected")
    split_days = []  # (date, multiplier to apply to all days STRICTLY BEFORE this date)
    for month_prev, month_cur, ratio in boundaries:
        found = locate_split_day(rows_sorted, month_prev, month_cur, ratio)
        if found is None:
            print(f"    WARNING: could not locate exact split day for {month_prev}->{month_cur} ratio={ratio}")
            continue
        diff, split_date, actual_ratio = found
        print(f"    split located: {split_date}  scale_ratio={ratio:.4f}  actual_price_ratio={actual_ratio:.4f}  (log-diff={diff:.3f})")
        # actual_ratio = raw_price_after/raw_price_before (<1 for a forward split);
        # dates BEFORE split_date must be multiplied by actual_ratio to reach the
        # post-split price basis that matches the (already-adjusted) ground truth.
        split_days.append((split_date, actual_ratio))

    split_days.sort()
    split_dates = [d for d, _ in split_days]
    cum_factors_after = [1.0] * len(split_days)
    running = 1.0
    for i in range(len(split_days) - 1, -1, -1):
        running *= split_days[i][1]
        cum_factors_after[i] = running

    def factor_for_date(d):
        # bisect_right: the split day itself is already at the NEW (post-split)
        # price level and must NOT be re-scaled by that split's own ratio.
        idx = bisect.bisect_right(split_dates, d)
        if idx >= len(split_days):
            return 1.0
        return cum_factors_after[idx]

    adjusted = [[d, p * factor_for_date(d)] for d, p in rows_sorted]

    resid_scales = compute_scales(adjusted, gt)
    if resid_scales:
        max_dev = max(abs(math.log(s)) for _, _, s in resid_scales)
        print(f"    post-adjustment residual max |log(scale)| = {max_dev:.4f} (0 = perfect match)")
        avg_resid_scale = sum(s for _, _, s in resid_scales) / len(resid_scales)
        adjusted = [[d, p * avg_resid_scale] for d, p in adjusted]

    return adjusted


def main():
    raw = json.load(open(RAW))
    out = {}
    for t in TICKERS:
        print(f"processing {t}...")
        if t == "T":
            rows = splice_t(raw)
        else:
            rows = raw.get(t, [])
        if not rows:
            print(f"  WARNING: no data for {t}")
            continue
        adjusted = adjust_series(t, rows)
        out[t] = adjusted
        print(f"  {t}: {len(adjusted)} rows, {adjusted[0][0]}={adjusted[0][1]:.2f} .. {adjusted[-1][0]}={adjusted[-1][1]:.2f}")

    with open(OUT, "w") as f:
        json.dump(out, f)
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
