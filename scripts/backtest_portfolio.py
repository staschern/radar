#!/usr/bin/env python3
"""Бэктест модельного портфеля с правилом частичной фиксации прибыли (ТЗ §10).

Портфель из тех же бумаг, что и модельный, с самой ранней доступной даты.
Ежемесячный взнос распределяется в бумагу с наибольшим недовесом.

Правило (по каждой бумаге отдельно, от средней цены покупки):
  фиксация  +50% / +62% / +75%  → продать по 1/6 базового объёма (итого 50%)
  откуп     −30% / −38% / −45%  от пика после фиксации → вернуть обратно
  ядро 50% позиции правилом не трогается

Освободившиеся средства паркуются в консервативный инструмент: выбирается
более доходный из ОФЗ (RGBITR) и фонда денежного рынка (ставка MOEXREPOEQ)
по доходности за последние 12 месяцев.

Дивиденды в ISS по отдельным бумагам недоступны, поэтому моделируются
равномерным потоком (по умолчанию 6% годовых) и реинвестируются. Buy&hold
держит больше акций и получает больше дивидендов — это учтено автоматически.

Использование:
    python3 scripts/backtest_portfolio.py
    python3 scripts/backtest_portfolio.py --monthly 20000 --div 8
"""
import argparse
import json
import pathlib

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "quotes"
TARGET = {"SBER": 13, "LKOH": 12, "SNGSP": 8, "PLZL": 8, "GMKN": 7,
          "ROSN": 7, "T": 6, "MOEX": 5, "YDEX": 4}
FIX_STEPS = (0.50, 0.62, 0.75)      # доходность позиции для фиксации
BUY_STEPS = (0.30, 0.38, 0.45)      # просадка от пика для откупа
FIX_SHARE = 0.50                    # какая доля позиции вообще фиксируется


def months(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))


class Position:
    """Одна бумага: количество, средняя цена, состояние правила фиксации."""

    def __init__(self):
        self.units = 0.0
        self.avg = 0.0
        self.base = 0.0        # объём, от которого считаются ядро и ступени
        self.fired = set()     # сработавшие ступени фиксации
        self.bought = set()    # сработавшие ступени откупа
        self.peak = 0.0        # максимум цены после первой фиксации
        self.parked = 0.0      # выведено в консервативный инструмент, ₽

    def buy(self, rub, price):
        u = rub / price
        self.avg = (self.avg * self.units + rub) / (self.units + u) if self.units + u else price
        self.units += u

    def ret(self, price):
        return price / self.avg - 1 if self.avg else 0.0


def load_prices():
    return json.loads((DATA / "prices.json").read_text(encoding="utf-8"))


def cons_index(dates, rgbitr, mmrate):
    """Индекс накопления консервативной части: помесячно выбираем инструмент
    с большей доходностью за последние 12 месяцев."""
    idx, val = {}, 1.0
    mm_acc, prev = 1.0, None
    for i, d in enumerate(dates):
        if prev is not None:
            r_bond = rgbitr.get(d, rgbitr.get(prev, 1)) / rgbitr.get(prev, 1) - 1
            rate = mmrate.get(d)
            r_mm = (rate / 100 / 12) if rate else None
            # оценка «что доходнее» — по последним 12 мес, без заглядывания вперёд
            look = dates[max(0, i - 12)]
            bond_12 = rgbitr.get(prev, 1) / rgbitr.get(look, 1) - 1 if rgbitr.get(look) else 0
            mm_12 = (mmrate.get(prev, 0) / 100) if mmrate.get(prev) else 0
            use_mm = r_mm is not None and mm_12 > bond_12
            val *= 1 + (r_mm if use_mm else r_bond)
        idx[d] = val
        prev = d
    return idx


def simulate(prices, dates, monthly, div_pct, use_rule, cons):
    pos = {t: Position() for t in TARGET}
    parked_units = 0.0          # паи консервативного инструмента
    equity, fixes, buys = [], 0, 0

    for d in dates:
        live = {t: prices[t][d] for t in TARGET if d in prices[t]}
        if not live:
            equity.append(0.0)
            continue
        pc = cons[d]

        # дивиденды за месяц — в общий пул к взносу
        div = sum(pos[t].units * live[t] for t in live) * (div_pct / 100 / 12)
        cash = monthly + div

        # взнос в бумагу с наибольшим недовесом
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
        if cash > 0:            # если все в перевесе — доливаем по весам
            for t in live:
                pos[t].buy(cash * TARGET[t] / wsum, live[t])

        if use_rule:
            for t, price in live.items():
                p = pos[t]
                if p.units <= 0:
                    continue
                if not p.fired:
                    p.base = p.units
                # фиксация
                for k, step in enumerate(FIX_STEPS):
                    if step not in p.fired and p.ret(price) >= step:
                        sell = min(p.base * FIX_SHARE / len(FIX_STEPS), p.units)
                        p.units -= sell
                        rub = sell * price
                        p.parked += rub
                        parked_units += rub / pc
                        p.fired.add(step)
                        p.peak = price
                        p.bought = set()
                        fixes += 1
                # откуп
                if p.fired and p.parked > 0:
                    p.peak = max(p.peak, price)
                    dd = price / p.peak - 1
                    for k, step in enumerate(BUY_STEPS):
                        if step not in p.bought and dd <= -step:
                            take = p.parked / (len(BUY_STEPS) - k)
                            take = min(take, parked_units * pc)
                            if take > 0:
                                p.buy(take, price)
                                p.parked -= take
                                parked_units -= take / pc
                                buys += 1
                            p.bought.add(step)
                    if p.parked <= 1e-6 or len(p.bought) == len(BUY_STEPS):
                        p.fired, p.bought, p.parked = set(), set(), max(p.parked, 0.0)
                        if p.parked <= 1e-6:
                            p.base = p.units

        equity.append(sum(pos[t].units * live[t] for t in live) + parked_units * pc)

    peak = mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak:
            mdd = min(mdd, v / peak - 1)
    return dict(final=equity[-1], mdd=mdd * 100, fixes=fixes, buys=buys,
                equity=equity, parked_end=parked_units * cons[dates[-1]])


def fmt(x):
    return f"{x:,.0f}".replace(",", " ")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--monthly", type=float, default=20000)
    ap.add_argument("--div", type=float, default=6.0, help="дивдоходность, %% годовых")
    ap.add_argument("--start", default="2007-07")
    a = ap.parse_args()

    prices = load_prices()
    rgbitr = {r["begin"][:7]: r["close"]
              for r in json.loads((DATA / "RGBITR_m.json").read_text(encoding="utf-8"))}
    mmrate = json.loads((DATA / "mmrate.json").read_text(encoding="utf-8"))
    dates = sorted(d for d in rgbitr if d >= a.start and any(d in prices[t] for t in TARGET))
    cons = cons_index(dates, rgbitr, mmrate)

    contributed = a.monthly * len(dates)
    print(f"Период {dates[0]} → {dates[-1]} ({len(dates)} мес, {len(dates)/12:.1f} года)")
    print(f"Взнос {fmt(a.monthly)} ₽/мес · всего внесено {fmt(contributed)} ₽ · "
          f"дивиденды {a.div:g}%/год (модель)\n")

    hold = simulate(prices, dates, a.monthly, a.div, False, cons)
    rule = simulate(prices, dates, a.monthly, a.div, True, cons)
    print(f"{'Стратегия':<24}{'Итог, ₽':>14}{'× взносов':>11}{'Просадка':>11}{'Срабатываний':>14}")
    print("-" * 74)
    print(f"{'Buy & hold':<24}{fmt(hold['final']):>14}"
          f"{hold['final']/contributed:>10.2f}×{hold['mdd']:>10.1f}%{'—':>14}")
    print(f"{'Правило фиксации':<24}{fmt(rule['final']):>14}"
          f"{rule['final']/contributed:>10.2f}×{rule['mdd']:>10.1f}%"
          f"{str(rule['fixes'])+' / '+str(rule['buys']):>14}")
    diff = (rule["final"] / hold["final"] - 1) * 100
    print(f"\nРазница: {diff:+.1f}%  ({fmt(rule['final'] - hold['final'])} ₽)")
    print(f"В консервативной части на конец периода: {fmt(rule['parked_end'])} ₽")


if __name__ == "__main__":
    main()
