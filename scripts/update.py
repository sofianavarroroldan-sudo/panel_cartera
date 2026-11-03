#!/usr/bin/env python3
"""Actualiza data.json: ultimo valor liquidativo de cada activo (Finect), tipo de cambio
USD/EUR del BCE (la misma fuente que el conversor del Banco de Espana) e indices de referencia.
Si algo falla, avisa en el log y NO borra datos anteriores."""
import csv, datetime as dt, io, json, re, time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

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
    soup = BeautifulSoup(html, "html.parser")
    text = clean(html)
    
    # Intento 1: buscar fecha mediante regex flexible
    m = DATE.search(text)
    d = None
    if m:
        day = m.group(1).zfill(2)
        month = m.group(2).zfill(2)
        year = m.group(3)
        d = f"{year}-{month}-{day}"
        
        # Buscar precio justo antes de la fecha (ventana de 300 caracteres)
        prices = PRICE.findall(text[max(0, m.start() - 300):m.start()])
        if prices:
            num, cur = prices[-1]
            return to_num(num), ("$" if cur in ("$", "USD") else "€"), d

    # Intento 2: si no hubo precio antes de la fecha, buscar en toda la página
    prices_all = PRICE.findall(text[:2500])
    if prices_all and d:
        num, cur = prices_all[0]
        return to_num(num), ("$" if cur in ("$", "USD") else "€"), d

    # Intento 3: buscar si la fecha aparece tras el texto "Datos a" sin regex estricto
    if "Datos a" in text:
        sub = text[text.find("Datos a"):text.find("Datos a") + 50]
        m2 = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", sub)
        if m2 and prices_all:
            d = f"{m2.group(3)}-{m2.group(2).zfill(2)}-{m2.group(1).zfill(2)}"
            num, cur = prices_all[0]
            return to_num(num), ("$" if cur in ("$", "USD") else "€"), d

    return None

def diagnose(name, r):
    text = clean(r.text)
    say(f"  [DIAGNOSTICO {name}] HTTP {r.status_code} | Longitud texto: {len(text)} chars")
    say(f"  Muestra inicial: {text[:220]!r}")

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

    for h in CFG["h"]:
        isin = h["i"]
        try:
            time.sleep(1.2)  # Pausa de cortesia para evitar deteccion antibot
            r = session.get(h["url"], timeout=30)
            r.raise_for_status()
            res = parse_finect(r.text)
            if not res:
                say(f"AVISO {isin}: no se encontro el valor liquidativo")
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

    # Benchmarks / Indices
    data.setdefault("bx", [{} for _ in CFG["benchmarks"]])
    for j, b in enumerate(CFG["benchmarks"]):
        if "url" not in b:
            continue
        try:
            time.sleep(1.2)
            r = session.get(b["url"], timeout=30)
            r.raise_for_status()
            res = parse_finect(r.text)
            if not res:
                say(f"AVISO indice {b['name']}: no se encontro valor")
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

    # Cobertura de fechas
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
