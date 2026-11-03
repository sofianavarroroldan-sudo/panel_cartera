#!/usr/bin/env python3
"""Actualiza data.json sin sobrescribir nunca datos existentes:
1. Intenta leer el ultimo valor de Finect (12 activos y 2 indices) si la fecha esta vacia.
2. Rellena con Yahoo Finance cualquier fecha vacia desde el inicio (18/10) para que las curvas y ratios funcionen."""
import csv, datetime as dt, io, json, re, time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data.json"

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

FINECT_URL_FIX = {
    "https://www.finect.com/etfs/IE00B4L5Y983": "https://www.finect.com/etfs/IE00B4L5Y983-Ishares_core_msci_world_etf_usd_acc",
    "https://www.finect.com/etfs/IE00B5BMR087": "https://www.finect.com/etfs/IE00B5BMR087-Ishares_core_sp_500_etf_usd_acc",
}

YF_BENCHMARKS = [
    ("MSCI World (ETF IWDA)", "IWDA.AS"),
    ("S&P 500 (ETF SXR8)", "SXR8.DE"),
]

MIN_COVERAGE = 0.5
log = []

def say(msg):
    print(msg)
    log.append(msg)

def to_num(s):
    return float(s.replace(".", "").replace(",", "."))

PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d+|\d+,\d+)\s*(\$|€|USD|EUR)")
DATE = re.compile(r"(?:Datos a|valor liquidativo:?|VL al:?|Fecha:?)\s*(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", re.I)

def clean(html):
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))

def parse_finect(html):
    text = clean(html)
    m = DATE.search(text)
    d = None
    if m:
        day = m.group(1).zfill(2)
        month = m.group(2).zfill(2)
        year = m.group(3)
        d = f"{year}-{month}-{day}"
        prices = PRICE.findall(text[max(0, m.start() - 300):m.start()])
        if prices:
            num, cur = prices[-1]
            return to_num(num), ("$" if cur in ("$", "USD") else "€"), d

    prices_all = PRICE.findall(text[:2500])
    if prices_all and d:
        num, cur = prices_all[0]
        return to_num(num), ("$" if cur in ("$", "USD") else "€"), d

    if "Datos a" in text:
        sub = text[text.find("Datos a"):text.find("Datos a") + 50]
        m2 = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", sub)
        if m2 and prices_all:
            d = f"{m2.group(3)}-{m2.group(2).zfill(2)}-{m2.group(1).zfill(2)}"
            num, cur = prices_all[0]
            return to_num(num), ("$" if cur in ("$", "USD") else "€"), d

    return None

def ecb_usd(start):
    r = requests.get(
        "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        params={"startPeriod": start, "format": "csvdata"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30
    )
    r.raise_for_status()
    return {row["TIME_PERIOD"]: float(row["OBS_VALUE"])
            for row in csv.DictReader(io.StringIO(r.text)) if row.get("OBS_VALUE")}

def fx_on(fx, d):
    ks = [k for k in fx if k <= d]
    return fx[max(ks)] if ks else None

def fetch_yahoo_isin(isin, start, fx, existing_px):
    """Busca el activo por ISIN en Yahoo Finance y devuelve la serie en EUR validada."""
    ref = existing_px[max(existing_px)] if existing_px else None
    candidates = []
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": isin, "quotesCount": 6, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        if r.ok:
            quotes = r.json().get("quotes", [])
            eur_sufs = (".DE", ".AS", ".PA", ".MC", ".F", ".MI")
            eur_q = [q["symbol"] for q in quotes if "symbol" in q and q["symbol"].endswith(eur_sufs)]
            other_q = [q["symbol"] for q in quotes if "symbol" in q and not q["symbol"].endswith(eur_sufs)]
            candidates.extend(eur_q + other_q)
    except Exception:
        pass
    candidates.append(isin)

    seen = set()
    for sym in candidates:
        if sym in seen:
            continue
        seen.add(sym)
        try:
            t = yf.Ticker(sym)
            hist = t.history(start=start)
            if hist.empty or "Close" not in hist:
                continue
            cur = None
            try:
                cur = t.fast_info.get("currency")
            except Exception:
                pass

            raw = {idx_dt.strftime("%Y-%m-%d"): float(val)
                   for idx_dt, val in hist["Close"].dropna().items()
                   if idx_dt.strftime("%Y-%m-%d") > start}
            if not raw:
                continue

            variantes = []
            if cur == "USD":
                variantes.append({d: round(v / fx_on(fx, d), 4) for d, v in raw.items() if fx_on(fx, d)})
                variantes.append({d: round(v, 4) for d, v in raw.items()})
            else:
                variantes.append({d: round(v, 4) for d, v in raw.items()})
                variantes.append({d: round(v / fx_on(fx, d), 4) for d, v in raw.items() if fx_on(fx, d)})

            for serie in variantes:
                if not serie:
                    continue
                if ref is None:
                    return sym, serie
                sample = serie[max(serie)]
                if abs(sample - ref) / ref <= 0.20:
                    return sym, serie
        except Exception:
            continue
    return None, {}

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    start = CFG["cfg"]["start"]
    changed = False

    session = requests.Session()
    session.headers.update(UA)

    try:
        fx = ecb_usd((dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat())
        say(f"BCE: {len(fx)} tipos de cambio descargados")
    except Exception as e:
        fx = {}
        say(f"AVISO: no se pudo leer el tipo de cambio del BCE ({e})")

    # 1. Activos de la cartera: primero Finect y luego relleno de huecos con Yahoo Finance
    for h in CFG["h"]:
        isin = h["i"]
        px = data["px"].setdefault(isin, {})

        try:
            time.sleep(1.2)
            url = FINECT_URL_FIX.get(h["url"], h["url"])
            r = session.get(url, timeout=30)
            r.raise_for_status()
            res = parse_finect(r.text)
            if res:
                price, cur, d = res
                if cur == "$":
                    rate = fx_on(fx, d)
                    price = (price / rate) if rate else None
                if price is not None:
                    price = round(price, 4)
                    if d > start and d not in px:
                        px[d] = price
                        changed = True
                        say(f"{isin} (Finect): {d} = {price} EUR")
            else:
                say(f"AVISO {isin}: sin dato hoy en Finect, buscando en Yahoo...")
        except Exception as e:
            say(f"AVISO {isin} en Finect ({e}), buscando en Yahoo...")

        sym, y_serie = fetch_yahoo_isin(isin, start, fx, px)
        if y_serie:
            nuevos = 0
            for d, val in y_serie.items():
                if d > start and d not in px:
                    px[d] = val
                    changed = True
                    nuevos += 1
            if nuevos > 0:
                say(f"{isin} (Yahoo {sym}): {nuevos} fechas vacias rellenadas")

    # 2. Indices de referencia: primero Finect y luego relleno de huecos con Yahoo Finance
    benchmarks = CFG.get("benchmarks", [])
    num_bx = max(len(benchmarks), len(YF_BENCHMARKS))
    data.setdefault("bx", [{} for _ in range(num_bx)])
    while len(data["bx"]) < num_bx:
        data["bx"].append({})

    for j, b in enumerate(benchmarks):
        if "url" not in b:
            continue
        try:
            time.sleep(1.2)
            url = FINECT_URL_FIX.get(b["url"], b["url"])
            r = session.get(url, timeout=30)
            r.raise_for_status()
            res = parse_finect(r.text)
            if res:
                price, cur, d = res
                if cur == "$":
                    rate = fx_on(fx, d)
                    price = (price / rate) if rate else None
                if price is not None:
                    price = round(price, 4)
                    if d >= start and d not in data["bx"][j]:
                        data["bx"][j][d] = price
                        changed = True
                        say(f"Indice {b['name']} (Finect): {d} = {price} EUR")
        except Exception as e:
            say(f"AVISO indice {b.get('name', j)} en Finect: {e}")

    for j, (b_name, ticker) in enumerate(YF_BENCHMARKS):
        try:
            hist = yf.Ticker(ticker).history(start=start)
            if not hist.empty and "Close" in hist:
                rellenados = 0
                for idx_dt, val in hist["Close"].dropna().items():
                    d = idx_dt.strftime("%Y-%m-%d")
                    if d >= start and d not in data["bx"][j]:
                        data["bx"][j][d] = round(float(val), 4)
                        changed = True
                        rellenados += 1
                if rellenados > 0:
                    say(f"Indice {b_name} (Yahoo {ticker}): {rellenados} huecos rellenados")
        except Exception as e:
            say(f"AVISO relleno Yahoo en {b_name}: {e}")

    # 3. Cobertura de fechas
    ids = [h["i"] for h in CFG["h"]]
    todas = set()
    for i in ids:
        todas |= set(data["px"].get(i, {}))
    keep = [d for d in sorted(todas) if d > start and
            sum(1 for i in ids if d in data["px"].get(i, {})) / len(ids) >= MIN_COVERAGE]

    if keep != data.get("dates"):
        data["dates"] = keep
        changed = True

    if changed:
        data["meta"] = {
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "log": log[-25:]
        }
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        say("data.json actualizado")
    else:
        say("Sin cambios")

if __name__ == "__main__":
    main()
