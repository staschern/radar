#!/usr/bin/env python3
"""Загрузка рыночных данных для «Утреннего радара» и модельного портфеля.

Первичные источники:
  * MOEX ISS API (iss.moex.com) — котировки акций, ОФЗ, БПИФ, индексы, лоты
  * ЦБ РФ (cbr.ru)              — официальные курсы валют

Требует, чтобы домены iss.moex.com и www.cbr.ru были разрешены сетевой
политикой окружения (Network access = Custom + Allowed domains, либо Full).
См. docs/knowledge/moex_iss_api.md.

Использование:
    python3 scripts/fetch_market_data.py --date 2026-08-18
    python3 scripts/fetch_market_data.py --date 2026-08-18 --tickers SBER,LKOH
    python3 scripts/fetch_market_data.py --check      # только проверка доступа

Результат: data/quotes/YYYY-MM-DD.json
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ISS = "https://iss.moex.com/iss"
CBR = "https://www.cbr.ru/scripts/XML_daily.asp"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "quotes"

# Режимы торгов, покрывающие допустимую вселенную активов (ТЗ Портфель §5)
# БПИФ торгуются в том же режиме TQBR, что и акции — отдельного борда для них нет
# (проверено 2026-08-18: у LQDT, TGLD, GOLD, AKMM primary board = TQBR).
BOARDS = {
    "shares": ("stock", "shares", "TQBR"),   # акции и БПИФ
    "bonds":  ("stock", "bonds", "TQOB"),    # ОФЗ
    "index":  ("stock", "index", "SNDX"),    # индексы (IMOEX, RGBI)
}

# Ограничение колонок обязательно: полный ответ по TQBR (500+ бумаг) рвёт соединение.
COLUMNS = {
    "shares": "SECID,SHORTNAME,CLOSE,OPEN,VOLUME",
    "bonds":  "SECID,SHORTNAME,CLOSE,YIELDCLOSE,MATDATE,FACEVALUE",
    "index":  "SECID,SHORTNAME,CLOSE,OPEN",
}


class Blocked(Exception):
    """Домен недоступен из-за сетевой политики окружения."""


def get(url: str, timeout: int = 40, tries: int = 3, encoding: str = "utf-8"):
    """GET с уважением к HTTPS_PROXY и CA-бандлу окружения.

    ISS периодически рвёт соединение на больших ответах, поэтому повторяем с
    нарастающей паузой. Отказ политики egress не ретраим — он не пройдёт.
    """
    import time
    req = urllib.request.Request(url, headers={"User-Agent": "radar/1.0"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode(encoding, "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code in (403, 407):
                raise Blocked(f"{url} → HTTP {e.code}: отказ политики egress") from e
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, OSError) as e:
            msg = str(e)
            if "403" in msg or "tunnel" in msg.lower():
                raise Blocked(f"{url} → отказ политики egress ({msg})") from e
            if attempt == tries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    return json.loads(raw) if ".json" in url else raw


def iss_board(engine: str, market: str, board: str, date: str, columns: str) -> list[dict]:
    """Итоги торгов по режиму за дату. Пустой список = торгов не было."""
    rows, start = [], 0
    while True:
        url = (f"{ISS}/history/engines/{engine}/markets/{market}/boards/{board}"
               f"/securities.json?date={date}&iss.meta=off&iss.only=history"
               f"&history.columns={columns}&start={start}")
        block = get(url).get("history", {})
        cols, data = block.get("columns", []), block.get("data", [])
        if not data:
            break
        rows += [dict(zip(cols, r)) for r in data]
        start += len(data)
        if len(data) < 100:
            break
    return rows


def iss_lotsize(engine: str, market: str, board: str, secid: str) -> int | None:
    """Размер лота одной бумаги. В истории торгов его нет — только в справочнике.

    Запрашиваем поштучно: выгрузка справочника целиком по TQBR (500+ бумаг)
    занимает минуты и часто обрывается.
    """
    url = (f"{ISS}/engines/{engine}/markets/{market}/boards/{board}"
           f"/securities/{secid}.json?iss.meta=off&iss.only=securities"
           f"&securities.columns=SECID,LOTSIZE")
    data = get(url).get("securities", {}).get("data", [])
    return data[0][1] if data else None


def iss_accrued_int(secid: str) -> float | None:
    """НКД по облигации: цена в истории торгов чистая, без накопленного купона."""
    url = (f"{ISS}/engines/stock/markets/bonds/boards/TQOB/securities/{secid}.json"
           f"?iss.meta=off&iss.only=securities&securities.columns=SECID,ACCRUEDINT")
    data = get(url).get("securities", {}).get("data", [])
    return data[0][1] if data else None


def cbr_rates(date: str) -> dict:
    """Официальные курсы ЦБ на дату (курс устанавливается на следующий день)."""
    d = dt.date.fromisoformat(date).strftime("%d/%m/%Y")
    xml = get(f"{CBR}?date_req={d}", encoding="cp1251")  # ЦБ отдаёт windows-1251
    import re
    out = {}
    for m in re.finditer(
        r'<Valute[^>]*>.*?<CharCode>(\w+)</CharCode>.*?'
        r'<Nominal>(\d+)</Nominal>.*?<Value>([\d,]+)</Value>.*?</Valute>', xml, re.S):
        code, nominal, value = m.group(1), int(m.group(2)), float(m.group(3).replace(",", "."))
        out[code] = round(value / nominal, 4)
    return {k: out[k] for k in ("USD", "EUR", "CNY") if k in out}


def check_access() -> int:
    """Диагностика: какие домены доступны из окружения."""
    targets = [(f"{ISS}/index.json", "iss.moex.com (котировки)"),
               (f"{CBR}?date_req=01/01/2026", "www.cbr.ru (курсы валют)")]
    failed = 0
    for url, label in targets:
        try:
            get(url, timeout=25)
            print(f"  ✅ {label}")
        except Blocked as e:
            print(f"  ❌ {label} — заблокировано политикой окружения")
            print(f"     {e}")
            failed += 1
        except Exception as e:
            print(f"  ⚠️  {label} — ошибка: {type(e).__name__}: {e}")
            failed += 1
    if failed:
        print("\nЧтобы разрешить: окружение → Network access = Custom →")
        print("Allowed domains: iss.moex.com, *.moex.com, *.cbr.ru")
        print("и обязательно отметить «Also include default list of common package managers».")
        print("Подробности: docs/knowledge/moex_iss_api.md")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=dt.date.today().isoformat(),
                    help="дата торгов YYYY-MM-DD (по умолчанию сегодня)")
    ap.add_argument("--tickers", default="",
                    help="фильтр: через запятую. Пусто = весь режим торгов")
    ap.add_argument("--check", action="store_true", help="только проверить доступ к источникам")
    args = ap.parse_args()

    if args.check:
        print("Проверка доступа к источникам данных:")
        return 1 if check_access() else 0

    wanted = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
    result = {"date": args.date, "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
              "source": "MOEX ISS + CBR", "boards": {}, "cbr": {}, "errors": []}

    for name, (engine, market, board) in BOARDS.items():
        try:
            rows = iss_board(engine, market, board, args.date, COLUMNS[name])
            out = {}
            for r in rows:
                sec = r.get("SECID")
                if not sec or (wanted and sec not in wanted):
                    continue
                rec = {"close": r.get("CLOSE"), "open": r.get("OPEN"),
                       "volume": r.get("VOLUME"), "name": r.get("SHORTNAME")}
                if name == "bonds":
                    rec.update(yield_close=r.get("YIELDCLOSE"), matdate=r.get("MATDATE"),
                               facevalue=r.get("FACEVALUE"))
                out[sec] = rec
            # Лоты и НКД тянем только для запрошенных бумаг: поштучный запрос
            # на 500 инструментов занял бы минуты.
            if wanted and name != "index":
                for sec in out:
                    try:
                        if name == "bonds":
                            out[sec]["accrued_int"] = iss_accrued_int(sec)
                            out[sec]["lotsize"] = 1
                        else:
                            out[sec]["lotsize"] = iss_lotsize(engine, market, board, sec)
                    except Exception as e:
                        result["errors"].append(f"{sec}: детали не получены: {e}")
            result["boards"][name] = out
            print(f"  {name:8} {board:6} — инструментов: {len(out)}")
        except Blocked as e:
            result["errors"].append(str(e))
            print(f"  {name:8} {board:6} — ЗАБЛОКИРОВАНО", file=sys.stderr)
        except Exception as e:
            result["errors"].append(f"{name}: {type(e).__name__}: {e}")
            print(f"  {name:8} {board:6} — ошибка: {e}", file=sys.stderr)

    try:
        result["cbr"] = cbr_rates(args.date)
        print(f"  курсы ЦБ — {result['cbr']}")
    except Blocked as e:
        result["errors"].append(str(e))
        print("  курсы ЦБ — ЗАБЛОКИРОВАНО", file=sys.stderr)
    except Exception as e:
        result["errors"].append(f"cbr: {type(e).__name__}: {e}")

    if not result["boards"] and not result["cbr"]:
        print("\nНи один источник не ответил. Запустите с --check для диагностики.",
              file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{args.date}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nСохранено: {path.relative_to(ROOT)}")
    if result["errors"]:
        print(f"С ошибками: {len(result['errors'])} — см. поле errors в файле")
    return 0


if __name__ == "__main__":
    sys.exit(main())
