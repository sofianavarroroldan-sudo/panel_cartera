#!/usr/bin/env python3
"""Actualiza data.json: ultimo valor liquidativo de cada activo (Finect), tipo de cambio
USD/EUR del BCE (la misma fuente que el conversor del Banco de Espana) e indices de referencia (todo de Finect, salvo el tipo de cambio).
Si algo falla, avisa en el log y NO borra datos anteriores."""
import csv, datetime as dt, io, json, re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "es-ES,es;q=0.9", "Referer": "https://www.finect.com/"}
MIN_COVERAGE = 0.5   # una fecha entra en el panel si al menos el 50 % de los activos tienen dato
log = []

def say(msg):
    print(msg)
    log.append(msg)

def to_num(s):
    return float(s.replace(".", "").replace(",", "."))

# En Finect aparece el precio ("49,97$") y, mas adelante, "Datos a 30/10/2026".
# Buscamos la fecha y tomamos el ultimo precio con divisa justo antes (aunque haya cambios del dia entre medias).
PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d+|\d+,\d+)\s*(\$|€|USD|EUR)")
DATE = re.compile(r"(?:Datos a|valor liquidativo:?)\s*(\d{2})/(\d{2})/(\d{4})", re.I)

def clean(html):
    return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))

def parse_finect(html):
    text = clean(html)
    m = DATE.search(text)
    if not m:
        return None
    prices = PRICE.findall(text[max(0, m.start() - 200):m.start()])
    if not prices:
        return None
    num, cur = prices[-1]
    return to_num(num), ("$" if cur in ("$", "USD") else "€"), f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

def diagnose(name, r):
    text = clean(r.text)
    i = text.find("Datos a")
    say(f"DIAGNOSTICO {name}: http={r.status_code}, {len(r.text)} bytes, 'Datos a' en posicion {i}")
    say(f"  inicio: {text[:160]!r}")
    if i >= 0:
        say(f"  contexto: {text[max(0, i - 160):i + 60]!r}")

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
                diagnose(isin, r)
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
            r = requests.get(b["url"], headers=UA, timeout=30)
            r.raise_for_status()
            res = parse_finect(r.text)
            if not res:
                say(f"AVISO indice {b['name']}: no se encontro el valor liquidativo (revisad el enlace de Finect en config.json)")
                diagnose(b["name"], r)
                continue
            price, cur, d = res
            if cur == "$":
                rate = fx_on(fx, d)
                if not rate:
                    say(f"AVISO indice {b['name']}: sin tipo de cambio para {d}")
                    continue
                price = price / rate
            price = round(price, 4)
            if d >= start and data["bx"][j].get(d) != price:
                data["bx"][j][d] = price
                changed = True
                say(f"Indice {b['name']}: {d} = {price} EUR")
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
