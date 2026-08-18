#!/usr/bin/env python3
"""Бэктест правила «зафиксировать после роста — откупать на падении».

Сравнивает две стратегии при одинаковых регулярных взносах:
  A. Buy&hold  — весь взнос идёт в акции, ничего не продаётся.
  B. Правило   — когда рост акций от последнего минимума достигает X%,
                 доля FIX акционной части продаётся в облигации;
                 обратно откупается частями по мере просадки от максимума.

Данные: MCFTR (акции полной доходности) и RGBITR (ОФЗ полной доходности),
месячные закрытия, MOEX ISS. Никакого заглядывания вперёд: на каждом шаге
используются только значения до текущего месяца включительно.

Использование:
    python3 scripts/backtest_timing.py
    python3 scripts/backtest_timing.py --monthly 10833 --fix 0.5
"""
import argparse
import json
import pathlib

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "quotes"
BUY_LEVELS = (0.10, 0.20, 0.30)   # просадки от максимума, на которых откупаем
DIV_YIELD_PRE2016 = 0.04          # реконструкция дивидендов до 2016 — допущение


def load(name: str) -> list[tuple[str, float]]:
    rows = json.loads((DATA / f"{name}_m.json").read_text(encoding="utf-8"))
    return [(r["begin"][:7], r["close"]) for r in rows]


def synth_total_return(imoex: list[tuple[str, float]],
                       div: float = DIV_YIELD_PRE2016) -> list[tuple[str, float]]:
    """Реконструкция полной доходности из ценового индекса.

    MCFTR существует только с 2016-11. Чтобы захватить кризисы 2008 и 2014,
    достраиваем ряд назад, добавляя к цене дивидендную доходность.
    Это ДОПУЩЕНИЕ, а не факт: реальная дивдоходность в 2000-е отличалась.
    """
    out, mult, m = [], 1.0, (1 + div) ** (1 / 12)
    for i, (d, v) in enumerate(imoex):
        out.append((d, v * mult))
        mult *= m
    return out


def run(stocks, bonds, monthly, mode, x_pct=None, fix=0.5):
    """Один прогон. mode: 'hold' | 'rule'. Возвращает метрики."""
    s_units = b_units = 0.0          # «паи» акций и облигаций
    contributed = 0.0
    run_min = stocks[0][1]
    run_max = stocks[0][1]
    armed = True                      # можно ли фиксировать в текущем цикле
    bought_back = set()               # какие уровни откупа уже сработали
    peak_at_fix = None
    equity = []

    for i, (date, px) in enumerate(stocks):
        pb = bonds[i][1]
        # взнос — всегда в акции
        s_units += monthly / px
        contributed += monthly

        run_min = min(run_min, px)
        run_max = max(run_max, px)

        if mode == "rule":
            growth = px / run_min - 1
            if armed and growth >= x_pct:
                # фиксируем долю акций в облигациях
                sell = s_units * fix
                s_units -= sell
                b_units += sell * px / pb
                armed = False
                peak_at_fix = px
                bought_back = set()
                run_max = px
            elif not armed and b_units > 0:
                dd = px / run_max - 1
                for k, lvl in enumerate(BUY_LEVELS):
                    if dd <= -lvl and lvl not in bought_back:
                        bought_back.add(lvl)
                        # откупаем равными долями оставшегося буфера
                        left = len(BUY_LEVELS) - k
                        buy = b_units / left
                        b_units -= buy
                        s_units += buy * pb / px
                if b_units < 1e-9 or len(bought_back) == len(BUY_LEVELS):
                    b_units = max(b_units, 0.0)
                    armed = True
                    run_min = px
        equity.append(s_units * px + b_units * pb)

    final = equity[-1]
    years = (len(stocks) - 1) / 12
    peak, mdd = 0.0, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak:
            mdd = min(mdd, v / peak - 1)
    return dict(final=final, contributed=contributed, years=years,
                multiple=final / contributed, mdd=mdd * 100)


def fmt(x): return f"{x:,.0f}".replace(",", " ")


def report(stocks, bonds, monthly, fix, label):
    print(f"\n{'='*78}\n{label}: {stocks[0][0]} → {stocks[-1][0]} "
          f"({(len(stocks)-1)/12:.1f} лет), взнос {fmt(monthly)} ₽/мес\n{'='*78}")
    hold = run(stocks, bonds, monthly, "hold")
    print(f"{'Стратегия':<28}{'Итог, ₽':>14}{'× к взносам':>13}{'Макс. просадка':>16}{'vs Buy&hold':>13}")
    print("-" * 78)
    print(f"{'Buy & hold':<28}{fmt(hold['final']):>14}{hold['multiple']:>12.2f}×"
          f"{hold['mdd']:>15.1f}%{'—':>13}")
    for x in (0.30, 0.50, 0.75, 1.00, 1.50):
        r = run(stocks, bonds, monthly, "rule", x, fix)
        diff = (r["final"] / hold["final"] - 1) * 100
        print(f"{'Фиксация при +' + str(int(x*100)) + '%':<28}{fmt(r['final']):>14}"
              f"{r['multiple']:>12.2f}×{r['mdd']:>15.1f}%{diff:>+12.1f}%")
    print(f"\nФиксируется {fix:.0%} акционной части. Откуп — тремя равными частями "
          f"при просадках {', '.join(f'{l:.0%}' for l in BUY_LEVELS)} от максимума.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--monthly", type=float, default=10833, help="взнос в месяц, ₽")
    ap.add_argument("--fix", type=float, default=0.5, help="доля акций к фиксации")
    a = ap.parse_args()

    mcftr, rgbitr, imoex = load("MCFTR"), load("RGBITR"), load("IMOEX")
    rb = {d: v for d, v in rgbitr}

    # 1. Фактические данные MCFTR
    pairs = [(d, v) for d, v in mcftr if d in rb]
    report(pairs, [(d, rb[d]) for d, _ in pairs], a.monthly, a.fix,
           "ФАКТИЧЕСКИЕ ДАННЫЕ (MCFTR + RGBITR)")

    # 2. Реконструкция с 2003 — чтобы захватить 2008 и 2014
    synth = [(d, v) for d, v in synth_total_return(imoex) if d >= "2003-01" and d in rb]
    report(synth, [(d, rb[d]) for d, _ in synth], a.monthly, a.fix,
           f"РЕКОНСТРУКЦИЯ (IMOEX + {DIV_YIELD_PRE2016:.0%} дивидендов) — ДОПУЩЕНИЕ")


if __name__ == "__main__":
    main()
