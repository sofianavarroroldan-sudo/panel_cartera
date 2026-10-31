#!/usr/bin/env python3
"""Actualiza data.json: ultimo valor liquidativo de cada activo (Finect), tipo de cambio
USD/EUR del BCE (la misma fuente que el conversor del Banco de Espana) e indices de referencia.
Si algo falla, avisa en el log y NO borra datos anteriores."""
import csv, datetime as dt, io, json, re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "es-ES,es;q=0.9"}
MIN_COVERAGE = 0.5   # una fecha entra en el panel si al menos el 50 % de los activos tienen dato
log = []

def say(msg):
    print(msg)
    log.append(msg)

def to_num(s):
    return float(s.replace(".", "").replace(",", "."))

# En Finect el precio aparece como "49,97$" seguido de "Datos a 30/10/2026"
RE = re.compile(r"([\d\.]+,\d+)\s*([$€])\s*Datos a\s*(\d{2})/(\d{2})/(\d{4})")

def parse_finect(html):
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    m = RE.search(text)
    if not m:
        return None
    return to_num(m.group(1)), m.group(2), f"{m.group(5)}-{m.group(4)}-{m.group(3)}"

def ecb_usd(start):
    """Dolares por euro (tipo de referencia del BCE), por dia."""
    r = requests.get("https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
                     params={"startPeriod": start, "format": "csvdata"}, headers=UA, timeout=30)
    r.raise_for_status()
    return {row["TIME_PERIOD"]: float(row["OBS_VALUE"])
            for row in csv.DictReader(io.StringIO(r.text)) if row.get("OBS_VALUE")}

def fx_on(fx, d):
    ks = [k for k in fx if k <= d]
    return fx[max(ks)] if ks else None

def bench_history(ticker, start):
    import yfinance as yf
    df = yf.download(ticker, start=start, progress=False, auto_adjust=False)
    out = {}
    if df is None or df.empty:
        return out
    col = df["Close"]
    if hasattr(col, "columns"):
        col = col.iloc[:, 0]
    for idx, v in col.items():
        out[idx.strftime("%Y-%m-%d")] = round(float(v), 4)
    return out

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    start = CFG["cfg"]["start"]
    changed = False
    try:
        fx = ecb_usd((dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat())
        say(f"BCE: {len(fx)} tipos de cambio descargados")
    except Exception as e:
        fx = {}
        say(f"AVISO: no se pudo leer el tipo de cambio del BCE ({e})")

    for h in CFG["h"]:
        isin = h["i"]
        try:
            r = requests.get(h["url"], headers=UA, timeout=30)
            r.raise_for_status()
            res = parse_finect(r.text)
            if not res:
                say(f"AVISO {isin}: no se encontro el valor liquidativo (Finect habra cambiado la pagina)")
                continue
            price, cur, d = res
            if cur == "$":
                rate = fx_on(fx, d)
                if not rate:
                    say(f"AVISO {isin}: sin tipo de cambio para {d}")
                    continue
                price = price / rate
            price = round(price, 4)
            if d > start:
                px = data["px"].setdefault(isin, {})
                if px.get(d) != price:
                    px[d] = price
                    changed = True
                    say(f"{isin}: {d} = {price} EUR")
        except Exception as e:
            say(f"AVISO {isin}: {e}")

    data.setdefault("bx", [{} for _ in CFG["benchmarks"]])
    for j, b in enumerate(CFG["benchmarks"]):
        try:
            hist = bench_history(b["yahoo"], start)
            new = {d: v for d, v in hist.items() if data["bx"][j].get(d) != v}
            if new:
                data["bx"][j].update(new)
                changed = True
                say(f"Indice {b['name']}: {len(new)} precios nuevos")
        except Exception as e:
            say(f"AVISO indice {b['name']}: {e}")

    # fechas con cobertura suficiente
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
        data["meta"] = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                        "log": log[-25:]}
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        say("data.json actualizado")
    else:
        say("Sin cambios")

if __name__ == "__main__":
    main()
