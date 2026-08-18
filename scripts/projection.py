#!/usr/bin/env python3
"""Долгосрочная проекция модельного портфеля при регулярных взносах.

Доходности классов активов взяты не с потолка, а из фактических индексов
полной доходности МосБиржи (MCFTR, RGBITR) и цены золота в рублях
(GLDRUB_TOM), выгруженных через MOEX ISS 2026-08-18. Источник каждой
цифры указан в SCENARIOS.

Использование:
    python3 scripts/projection.py                      # 2500 ₽/нед, 20 лет
    python3 scripts/projection.py --weekly 5000 --years 20
    python3 scripts/projection.py --target 20000000    # обратная задача
"""
import argparse

# Целевые доли профиля (docs/knowledge/investor_profile.md)
WEIGHTS = {"акции": 0.70, "облигации": 0.13, "золото": 0.12, "кэш": 0.05}

# Годовая доходность по классам, % — привязка к фактическим данным:
#   акции      MCFTR: 2007→2026 = 5.86% | 2003→2026 = 10.93% | 2003→2025 = 12.83%
#   облигации  RGBITR: 5 лет = 4.60% | 10 лет = 6.72% | 23.6 года = 8.89%
#   золото     GLDRUB_TOM 2013→2026 = 18.43% — намеренно срезано:
#              период включает девальвации 2014 и 2022, экстраполировать нельзя
#   кэш        фонд денежного рынка, следует за ключевой ставкой
SCENARIOS = {
    "Негативный": {"акции": 5.9, "облигации": 4.6, "золото": 5.0, "кэш": 5.0},
    "Базовый":    {"акции": 10.9, "облигации": 6.7, "золото": 8.0, "кэш": 7.0},
    "Позитивный": {"акции": 12.8, "облигации": 8.9, "золото": 12.0, "кэш": 8.0},
}

# Издержки: TER фондов на золотой и денежной доле. ИИС типа Б — налога нет,
# брокерская комиссия 0.05% с взноса ≈ 1.25 ₽/нед, в годовых пренебрежимо.
COSTS_PCT = 0.10
WEEKS = 52


def portfolio_return(scenario: dict) -> float:
    """Взвешенная годовая доходность портфеля за вычетом издержек, %."""
    return sum(WEIGHTS[k] * scenario[k] for k in WEIGHTS) - COSTS_PCT


def future_value(weekly: float, years: float, annual_pct: float,
                 indexation_pct: float = 0.0) -> tuple[float, float]:
    """Будущая стоимость при еженедельных взносах.

    indexation_pct — ежегодная индексация размера взноса (защита от инфляции).
    Возвращает (итоговая стоимость, всего внесено).
    """
    i = (1 + annual_pct / 100) ** (1 / WEEKS) - 1
    value, contributed, payment = 0.0, 0.0, weekly
    for week in range(int(years * WEEKS)):
        if week and week % WEEKS == 0:
            payment *= 1 + indexation_pct / 100
        value = value * (1 + i) + payment
        contributed += payment
    return value, contributed


def required_weekly(target: float, years: float, annual_pct: float) -> float:
    """Обратная задача: какой еженедельный взнос даёт целевую сумму."""
    i = (1 + annual_pct / 100) ** (1 / WEEKS) - 1
    n = int(years * WEEKS)
    return target * i / ((1 + i) ** n - 1)


def fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weekly", type=float, default=2500)
    ap.add_argument("--years", type=float, default=20)
    ap.add_argument("--target", type=float, default=20_000_000)
    ap.add_argument("--indexation", type=float, default=0.0,
                    help="ежегодная индексация взноса, %% (например 6)")
    a = ap.parse_args()

    print(f"Взнос {fmt(a.weekly)} ₽/нед · горизонт {a.years:g} лет · "
          f"индексация взноса {a.indexation:g}%/год\n")
    print(f"{'Сценарий':<12}{'Доходн.':>9}{'Итог, ₽':>15}{'Внесено, ₽':>14}"
          f"{'Прибыль, ₽':>15}{'×':>7}")
    print("-" * 72)
    for name, sc in SCENARIOS.items():
        r = portfolio_return(sc)
        fv, contributed = future_value(a.weekly, a.years, r, a.indexation)
        print(f"{name:<12}{r:>8.2f}%{fmt(fv):>15}{fmt(contributed):>14}"
              f"{fmt(fv - contributed):>15}{fv / contributed:>6.1f}×")

    print(f"\nСколько нужно вкладывать еженедельно ради {fmt(a.target)} ₽ "
          f"за {a.years:g} лет:")
    print("-" * 72)
    for name, sc in SCENARIOS.items():
        r = portfolio_return(sc)
        w = required_weekly(a.target, a.years, r)
        print(f"{name:<12}{r:>8.2f}%   {fmt(w):>9} ₽/нед   "
              f"({fmt(w * 52)} ₽/год, всего внесёте {fmt(w * 52 * a.years)} ₽)")

    print(f"\nЧто такое {fmt(a.target)} ₽ через {a.years:g} лет в сегодняшних деньгах:")
    for infl in (4.0, 6.0):
        print(f"  при инфляции {infl:g}%/год — {fmt(a.target / (1 + infl / 100) ** a.years)} ₽")


if __name__ == "__main__":
    main()
