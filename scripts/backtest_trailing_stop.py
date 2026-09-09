#!/usr/bin/env python3
"""Дневной бэктест: дискретное правило §10 (уровни фиксации/откупа) против
трейлинг-стоп варианта той же идеи.

Дискретное правило (текущие уровни ТЗ §10.3/10.4 — ВНИМАНИЕ:
scripts/backtest_portfolio.py использует устаревшие +50/62/75 / -30/38/45
из версии ТЗ до v1.4, здесь — актуальные):
  фиксация  +75% / +90% / +110% (доходность позиции) -> продать по 1/3 тактической части (=1/6 базы)
  откуп     -20% / -28% / -35% от пика цены после последней продажи -> вернуть по формуле parked/(остаток ступеней)
  ядро 50% не трогается

Трейлинг вариант: при достижении уровня НЕ продаём/докупаем сразу, а
"взводим" соответствующую долю и ждём отката/отскока на pullback_pct от
достигнутого пика/дна (созданного уже ПОСЛЕ взведения). Чем больше уровней
пересечено без отката — тем больше доля срабатывает одним движением.

Входные данные — data/quotes/daily_prices_9stocks_adjusted.json (см.
scripts/adjust_stock_splits.py) и data/quotes/RGBITR_daily.json (парковка
средств после фиксации моделируется через дневную доходность индекса
гособлигаций полной доходности).

См. запись от 2026-09-09 в docs/knowledge/research_log.md для методологии
и итоговых цифр.
"""
import json, datetime, bisect, argparse

TARGET = {"SBER": 13, "LKOH": 12, "SNGSP": 8, "PLZL": 8, "GMKN": 7,
          "ROSN": 7, "T": 6, "MOEX": 5, "YDEX": 4}
FIX_STEPS = (0.75, 0.90, 1.10)
BUY_STEPS = (0.20, 0.28, 0.35)
FIX_SHARE = 0.50


def load_daily_prices():
    with open("data/quotes/daily_prices_9stocks_adjusted.json") as f:
        raw = json.load(f)
    out = {}
    for t, rows in raw.items():
        out[t] = {r[0]: r[1] for r in rows}
    return out


def load_rgbitr():
    rows = json.load(open("data/quotes/RGBITR_daily.json"))
    return {r[0]: r[1] for r in rows}


class Pos:
    __slots__ = ("units", "avg", "base", "fired_sell", "fired_buy",
                 "pending_sell", "sell_peak", "pending_buy", "buy_trough",
                 "parked", "fix_peak")

    def __init__(self):
        self.units = 0.0
        self.avg = 0.0
        self.base = 0.0
        self.fired_sell = set()
        self.fired_buy = set()
        self.pending_sell = 0.0   # доля БАЗЫ, взведённая на продажу
        self.sell_peak = None
        self.pending_buy = 0      # число взведённых, но не исполненных ступеней откупа
        self.buy_trough = None
        self.parked = 0.0
        self.fix_peak = None      # пик цены после последней ПРОДАЖИ (для отсчёта просадки на откуп)

    def buy(self, rub, price):
        if price <= 0:
            return
        u = rub / price
        tot = self.units + u
        self.avg = (self.avg * self.units + rub) / tot if tot else price
        self.units = tot

    def ret(self, price):
        return price / self.avg - 1 if self.avg else 0.0


def union_sorted_dates(price_dict):
    s = set()
    for t in price_dict:
        s |= set(price_dict[t].keys())
    return sorted(s)


def month_key(d):
    return d[:7]


def xirr(cashflows):
    """cashflows: list of (date_str, amount). Negative = outflow (contribution), positive = inflow (terminal value).
    Returns annualized rate via bisection on NPV=0."""
    d0 = datetime.date.fromisoformat(cashflows[0][0])
    times = [(datetime.date.fromisoformat(d) - d0).days / 365.0 for d, _ in cashflows]
    amounts = [a for _, a in cashflows]

    def npv(r):
        return sum(a / (1 + r) ** t for a, t in zip(amounts, times))

    lo, hi = -0.99, 10.0
    flo, fhi = npv(lo), npv(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-6:
            return mid
        if flo * fm < 0:
            hi = mid
            fhi = fm
        else:
            lo = mid
            flo = fm
    return (lo + hi) / 2


def simulate(prices, all_dates, monthly, div_pct_annual, mode, pullback=None, verbose_events=False):
    """mode: 'hold' | 'discrete' | 'trailing'"""
    pos = {t: Pos() for t in TARGET}
    events = []

    rgbitr = load_rgbitr()
    rg_dates = sorted(rgbitr.keys())
    rg_vals = [rgbitr[d] for d in rg_dates]

    def get_rgbitr(d):
        i = bisect.bisect_right(rg_dates, d) - 1
        if i < 0:
            i = 0
        return rg_vals[i]

    rg_prev = None
    last_month = None
    peak_eq = 0.0
    mdd = 0.0
    n_fix = n_buy = 0
    cashflows = []
    equity_curve = []

    for d in all_dates:
        live = {t: prices[t][d] for t in TARGET if d in prices[t]}
        if not live:
            continue

        mk = month_key(d)
        is_new_month = (mk != last_month)
        if is_new_month:
            last_month = mk
            cashflows.append((d, -monthly))
            div = sum(pos[t].units * live.get(t, 0) for t in pos) * (div_pct_annual / 100 / 12)
            cash = monthly + div
            wsum = sum(TARGET[t] for t in live)
            total_eq = sum(pos[t].units * live[t] for t in live)
            gaps = []
            for t in live:
                target_rub = (total_eq + cash) * TARGET[t] / wsum
                gaps.append((target_rub - pos[t].units * live[t], t))
            gaps.sort(reverse=True)
            for gap, t in gaps:
                if cash <= 0:
                    break
                take = min(cash, max(gap, 0))
                if take <= 0:
                    continue
                pos[t].buy(take, live[t])
                cash -= take
            if cash > 0:
                for t in live:
                    pos[t].buy(cash * TARGET[t] / wsum, live[t])

        if mode != "hold":
            for t, price in live.items():
                p = pos[t]
                if p.units <= 0:
                    continue
                if not p.fired_sell and not p.fired_buy and p.parked <= 1e-9:
                    p.base = p.units  # новый цикл

                # --- продажная сторона ---
                for lvl in FIX_STEPS:
                    if lvl not in p.fired_sell and p.ret(price) >= lvl:
                        p.fired_sell.add(lvl)
                        if mode == "discrete":
                            sell_u = min(p.base * FIX_SHARE / len(FIX_STEPS), p.units)
                            p.units -= sell_u
                            rub = sell_u * price
                            p.parked += rub
                            p.fix_peak = price
                            n_fix += 1
                            if verbose_events:
                                events.append((d, t, "SELL", price, sell_u))
                        else:  # trailing: взводим
                            p.pending_sell += FIX_SHARE / len(FIX_STEPS)
                            p.sell_peak = price if p.sell_peak is None else max(p.sell_peak, price)

                if mode == "trailing" and p.pending_sell > 0:
                    p.sell_peak = max(p.sell_peak, price)
                    if price <= p.sell_peak * (1 - pullback):
                        sell_u = min(p.pending_sell * p.base, p.units)
                        p.units -= sell_u
                        rub = sell_u * price
                        p.parked += rub
                        p.fix_peak = price
                        n_fix += 1
                        if verbose_events:
                            events.append((d, t, "SELL(trail)", price, sell_u))
                        p.pending_sell = 0.0
                        p.sell_peak = None

                # --- откупная сторона ---
                if p.parked > 1e-9:
                    p.fix_peak = price if p.fix_peak is None else max(p.fix_peak, price)
                    dd = price / p.fix_peak - 1
                    for k, lvl in enumerate(BUY_STEPS):
                        if lvl not in p.fired_buy and dd <= -lvl:
                            p.fired_buy.add(lvl)
                            if mode == "discrete":
                                remaining = len(BUY_STEPS) - k
                                take = p.parked / remaining
                                take = min(take, p.parked)
                                if take > 0:
                                    p.buy(take, price)
                                    p.parked -= take
                                    n_buy += 1
                                    if verbose_events:
                                        events.append((d, t, "BUY", price, take))
                            else:  # trailing: взводим
                                p.pending_buy += 1
                                p.buy_trough = price if p.buy_trough is None else min(p.buy_trough, price)

                    if mode == "trailing" and p.pending_buy > 0:
                        p.buy_trough = min(p.buy_trough, price)
                        if price >= p.buy_trough * (1 + pullback):
                            # исполняем все взведённые ступени последовательно по той же формуле "остаток/число оставшихся"
                            n_levels_total = len(BUY_STEPS)
                            already_done = len(p.fired_buy) - p.pending_buy
                            for i in range(p.pending_buy):
                                remaining = n_levels_total - (already_done + i)
                                take = p.parked / remaining if remaining > 0 else p.parked
                                take = min(take, p.parked)
                                if take > 0:
                                    p.buy(take, price)
                                    p.parked -= take
                                    n_buy += 1
                            if verbose_events:
                                events.append((d, t, "BUY(trail)", price, None))
                            p.pending_buy = 0
                            p.buy_trough = None

                    if p.parked <= 1e-6 and len(p.fired_buy) >= len(BUY_STEPS):
                        p.fired_sell, p.fired_buy = set(), set()
                        p.parked = 0.0
                        p.fix_peak = None
                        p.base = p.units

        # компаундируем все парковки (облигации/денежный рынок = RGBITR) на дневной доходности индекса
        rg_now = get_rgbitr(d)
        if rg_prev is not None and rg_prev > 0:
            factor = rg_now / rg_prev
            for t in pos:
                if pos[t].parked > 0:
                    pos[t].parked *= factor
        rg_prev = rg_now

        stock_eq = sum(pos[t].units * live[t] for t in live)
        parked_eq = sum(pos[t].parked for t in pos)
        total_eq = stock_eq + parked_eq

        peak_eq = max(peak_eq, total_eq)
        if peak_eq > 0:
            mdd = min(mdd, total_eq / peak_eq - 1)
        equity_curve.append((d, total_eq))

    final_value = equity_curve[-1][1] if equity_curve else 0.0
    cashflows_full = cashflows + [(equity_curve[-1][0], final_value)] if equity_curve else cashflows
    irr = xirr(cashflows_full) if len(cashflows_full) > 1 else None

    return dict(n_fix=n_fix, n_buy=n_buy, final=final_value, mdd=mdd, irr=irr,
                n_months=len(cashflows), events=events)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--monthly", type=float, default=20000.0)
    ap.add_argument("--div", type=float, default=6.0)
    args = ap.parse_args()

    prices = load_daily_prices()
    all_dates = union_sorted_dates(prices)
    d0, d1 = all_dates[0], all_dates[-1]
    years = (datetime.date.fromisoformat(d1) - datetime.date.fromisoformat(d0)).days / 365.25
    print(f"period: {d0} .. {d1}  ({years:.1f} years, {len(all_dates)} trading days)")
    print(f"monthly contribution: {args.monthly:.0f} RUB, dividend yield assumption: {args.div:.1f}%/year\n")

    runs = []
    r = simulate(prices, all_dates, args.monthly, args.div, "hold")
    runs.append(("Buy & Hold", r))
    r = simulate(prices, all_dates, args.monthly, args.div, "discrete")
    runs.append(("Discrete (+75/90/110 / -20/28/35)", r))
    for pb in (0.03, 0.04, 0.05):
        r = simulate(prices, all_dates, args.monthly, args.div, "trailing", pullback=pb)
        runs.append((f"Trailing stop {int(pb*100)}%", r))

    print(f"{'Strategy':<38}{'Final, mln RUB':>16}{'IRR':>10}{'MaxDD':>10}{'Fix':>6}{'Buy':>6}")
    for name, r in runs:
        irr_s = f"{r['irr']*100:.1f}%" if r['irr'] is not None else "n/a"
        print(f"{name:<38}{r['final']/1e6:>16.2f}{irr_s:>10}{r['mdd']*100:>9.1f}%{r['n_fix']:>6}{r['n_buy']:>6}")


if __name__ == "__main__":
    main()
