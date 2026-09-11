#!/usr/bin/env python3
"""Бэктест: доля акций/облигаций линейно привязана к ключевой ставке ЦБ РФ.

Гипотеза заказчика (11.09.2026): минимум доли одного класса относительно
другого — 15% (максимум — 85%). При исторически максимальной ставке — 85%
облигаций/15% акций (защитно). При исторически минимальной — 85% акций/15%
облигаций (агрессивно). Между экстремумами — линейная интерполяция по
текущему значению ставки.

Прокси: MCFTR (акции, индекс МосБиржи полной доходности) и RGBITR
(облигации, индекс гособлигаций полной доходности) — сознательно широкие
индексы, не 9 бумаг модельного портфеля, чтобы изолировать эффект правила
распределения между классами от качества отбора бумаг внутри класса.

Ставка — полная история решений ЦБ РФ, 66 изменений, 13.09.2013-27.07.2026
(data/quotes/cbr_keyrate_changes.txt, первичный источник cbr.ru/hd_base/keyrate/).
Минимум 4,25% (27.07.2020), максимум 21,00% (28.10.2024 — выше шоков 2014 и
2022 годов, это пик цикла ужесточения 2023-2024).

См. запись от 2026-09-11 в docs/knowledge/research_log.md для методологии,
устойчивости по разным датам старта и полного разбора результатов.
"""
import json, datetime, bisect, argparse

RATE_MIN_FULL = 4.25
RATE_MAX_FULL = 21.0


def load_rate_changes():
    changes = []
    with open("data/quotes/cbr_keyrate_changes.txt") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            d, r = parts
            try:
                changes.append((d, float(r)))
            except ValueError:
                continue
    changes.sort()
    return changes


def load_index(path):
    rows = json.load(open(path))
    return {r[0]: r[1] for r in rows}


def union_sorted_dates(*dicts):
    s = set()
    for d in dicts:
        s |= set(d.keys())
    return sorted(s)


def rate_on(date, changes, dates_sorted):
    i = bisect.bisect_right(dates_sorted, date) - 1
    if i < 0:
        return changes[0][1]
    return changes[i][1]


def target_stock_weight(rate, rmin, rmax):
    if rmax <= rmin:
        return 50.0
    rate = max(min(rate, rmax), rmin)
    frac_from_max = (rmax - rate) / (rmax - rmin)  # 0 at max rate, 1 at min rate
    return 15.0 + frac_from_max * 70.0  # 15% at max rate, 85% at min rate


def simulate(stock_idx, bond_idx, all_dates, mode, start_date, end_date):
    """mode: 'static5050' | 'static_current' (~84/16) | 'dynamic_full' | 'dynamic_expanding'
    Ежемесячная ребалансировка к целевой доле. Без взносов - разовая сумма."""
    changes = load_rate_changes()
    change_dates = [c[0] for c in changes]

    dates = [d for d in all_dates if start_date <= d <= end_date]
    if not dates:
        return None

    stock_units = None
    bond_units = None
    last_month = None
    peak_eq = 0.0
    mdd = 0.0
    seen_rates = []

    equity_curve = []

    for d in dates:
        if d not in stock_idx or d not in bond_idx:
            continue
        sp = stock_idx[d]
        bp = bond_idx[d]

        mk = d[:7]
        is_new_month = mk != last_month
        r = rate_on(d, changes, change_dates)
        seen_rates.append(r)

        def weight():
            if mode == "static5050":
                return 50.0
            if mode == "static_current":
                return 70.0 / (70.0 + 13.0) * 100.0
            if mode == "dynamic_full":
                return target_stock_weight(r, RATE_MIN_FULL, RATE_MAX_FULL)
            if mode == "dynamic_expanding":
                return target_stock_weight(r, min(seen_rates), max(seen_rates))
            raise ValueError(mode)

        if stock_units is None:
            sw = weight()
            total = 1_000_000.0
            stock_units = total * sw / 100 / sp
            bond_units = total * (100 - sw) / 100 / bp
            last_month = mk
        elif is_new_month:
            last_month = mk
            sw = weight()
            total = stock_units * sp + bond_units * bp
            stock_units = total * sw / 100 / sp
            bond_units = total * (100 - sw) / 100 / bp

        total_eq = stock_units * sp + bond_units * bp
        peak_eq = max(peak_eq, total_eq)
        if peak_eq > 0:
            mdd = min(mdd, total_eq / peak_eq - 1)
        equity_curve.append((d, total_eq))

    if not equity_curve:
        return None

    d0, v0 = equity_curve[0]
    d1, v1 = equity_curve[-1]
    years = (datetime.date.fromisoformat(d1) - datetime.date.fromisoformat(d0)).days / 365.25
    cagr = (v1 / v0) ** (1 / years) - 1 if years > 0 else 0.0

    return dict(final=v1, cagr=cagr, mdd=mdd, years=years, start=d0, end=d1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013-09-17")
    ap.add_argument("--end", default="2026-09-07")
    args = ap.parse_args()

    stock_idx = load_index("data/quotes/MCFTR_daily.json")
    bond_idx = load_index("data/quotes/RGBITR_daily.json")
    all_dates = union_sorted_dates(stock_idx, bond_idx)

    modes = [
        ("Статика 50/50", "static5050"),
        ("Статика тек. ориентир (~84/16)", "static_current"),
        ("Динамика по ставке (15-85, полная история)", "dynamic_full"),
        ("Динамика по ставке (15-85, расширяющееся окно)", "dynamic_expanding"),
    ]

    print(f"Период: {args.start} .. {args.end}\n")
    print(f"{'Стратегия':<46}{'Итог, млн р.':>14}{'CAGR':>9}{'MaxDD':>9}")
    for name, mode in modes:
        r = simulate(stock_idx, bond_idx, all_dates, mode, args.start, args.end)
        if r is None:
            print(f"{name:<46}{'нет данных':>14}")
            continue
        print(f"{name:<46}{r['final']/1e6:>14.3f}{r['cagr']*100:>8.2f}%{r['mdd']*100:>8.1f}%")

    print("\n--- Робастность: разные даты старта, всегда до 2026-09-07 ---\n")
    starts = ["2013-09-17", "2015-01-01", "2017-01-01", "2019-01-01", "2020-08-01", "2022-03-01", "2023-01-01"]
    print(f"{'Старт':<12}{'50/50':>9}{'84/16':>9}{'Дин.полн':>10}{'Дин.расш':>10}   (CAGR %)")
    for st in starts:
        row = [st]
        for _, mode in modes:
            r = simulate(stock_idx, bond_idx, all_dates, mode, st, args.end)
            row.append(f"{r['cagr']*100:.2f}" if r else "—")
        print(f"{row[0]:<12}{row[1]:>9}{row[2]:>9}{row[3]:>10}{row[4]:>10}")

    print(f"\n{'Старт':<12}{'50/50':>9}{'84/16':>9}{'Дин.полн':>10}{'Дин.расш':>10}   (MaxDD %)")
    for st in starts:
        row = [st]
        for _, mode in modes:
            r = simulate(stock_idx, bond_idx, all_dates, mode, st, args.end)
            row.append(f"{r['mdd']*100:.1f}" if r else "—")
        print(f"{row[0]:<12}{row[1]:>9}{row[2]:>9}{row[3]:>10}{row[4]:>10}")


if __name__ == "__main__":
    main()
