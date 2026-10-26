#!/usr/bin/env python3
"""Raccoglie sagre e feste di paese da piu' fonti e produce docs/sagre.json
nel formato letto dall'app iOS (vedi README)."""
import csv, io, json, os, re, sys, time, unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "sagre.json"
CACHE = ROOT / "data" / "geocache.json"
MANUALI = ROOT / "data" / "manuali.csv"
UA = "SagreItaliaCollector/1.0 (%s)" % os.environ.get("CONTACT_EMAIL", "contatto-non-impostato")
MAX_NUOVI_GEOCODE = 300          # per esecuzione; il resto al giro successivo
ITALIA = (35.4, 47.2, 6.5, 18.8)  # lat min, lat max, lon min, lon max

MESI = {m: i for i, m in enumerate(
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre".split(), 1)}
CATEGORIE = [("tartuf", "Tartufo"), ("castagn", "Castagne"), ("marron", "Castagne"), ("vino", "Vino"),
    ("uva", "Vino"), ("zucca", "Zucca"), ("polenta", "Polenta"), ("pesce", "Pesce"), ("funghi", "Funghi"),
    ("porcin", "Funghi"), ("olio", "Olio"), ("formagg", "Formaggi"), ("salsicc", "Carne"),
    ("porchett", "Carne"), ("pane", "Pane"), ("dolc", "Dolci"), ("torron", "Dolci"), ("pasta", "Pasta"),
    ("tortell", "Pasta"), ("birra", "Birra")]

# ---------- utilita' ----------
def fold(s):
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()

def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", fold(s)).strip()

def slug(s):
    return norm(s).replace(" ", "-")[:60]

def categoria_da(testo):
    t = fold(testo)
    for k, c in CATEGORIE:
        if re.search(r"\b" + k, t):
            return c
    return "Sagra"

def trova_date(testo, anno=None):
    """Ritorna (inizio, fine) dalla prima/ultima data trovata nel testo, oppure None."""
    t, anno = fold(testo), anno or date.today().year
    m = re.search(r"(\d{1,2})\s*(?:-|al|a)\s*(\d{1,2})\s+([a-z]+)\s*(\d{4})?", t)
    if m and m.group(3) in MESI:
        try:
            a, mo = int(m.group(4) or anno), MESI[m.group(3)]
            return date(a, mo, int(m.group(1))), date(a, mo, int(m.group(2)))
        except ValueError:
            pass
    trovate = []
    for g in re.finditer(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})[/.](\d{1,2})[/.](\d{4})|(\d{1,2})\s+([a-z]+)\s*(\d{4})?", t):
        try:
            if g.group(1):
                trovate.append(date(int(g.group(1)), int(g.group(2)), int(g.group(3))))
            elif g.group(4):
                trovate.append(date(int(g.group(6)), int(g.group(5)), int(g.group(4))))
            elif g.group(8) in MESI:
                trovate.append(date(int(g.group(9) or anno), MESI[g.group(8)], int(g.group(7))))
        except ValueError:
            pass
    return (trovate[0], trovate[-1]) if trovate else None

def data_da(v):
    if isinstance(v, date):
        return v
    r = trova_date(str(v)) if v else None
    return r[0] if r else None

def http(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r

def prendi(obj, path):
    for k in (path or "").split("."):
        if k:
            obj = obj.get(k) if isinstance(obj, dict) else None
    return obj

# ---------- adattatori delle fonti ----------
def da_ical(src):
    from icalendar import Calendar
    cal = Calendar.from_ical(http(src["url"]).content)
    for ev in cal.walk("VEVENT"):
        ini = ev.decoded("DTSTART")
        fi = ev.decoded("DTEND", ini)
        tutto_giorno = not isinstance(ini, datetime)
        ini_d = ini if tutto_giorno else ini.date()
        fi_d = fi if not isinstance(fi, datetime) else fi.date()
        if tutto_giorno and fi_d > ini_d:
            fi_d -= timedelta(days=1)   # DTEND dei giorni interi e' esclusivo
        luogo = str(ev.get("LOCATION", ""))
        yield {"nome": str(ev.get("SUMMARY", "")), "comune": luogo.split(",")[0] if luogo else None,
               "inizio": ini_d, "fine": fi_d, "descrizione": str(ev.get("DESCRIPTION", "")) or None,
               "url": str(ev.get("URL", "")) or None}

def da_json(src):
    dati = http(src["url"]).json()
    voci = prendi(dati, src["elementi"]) if src.get("elementi") else dati
    for v in voci:
        yield {k: prendi(v, p) for k, p in src["campi"].items()}

def righe_csv(testo, src):
    for riga in csv.DictReader(io.StringIO(testo), delimiter=src.get("delimitatore", ",")):
        yield {k: riga.get(c) for k, c in src["campi"].items()}

def da_csv(src):
    yield from righe_csv(http(src["url"]).content.decode(src.get("encoding", "utf-8")), src)

def da_html(src):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(http(src["url"]).text, "html.parser")
    s = src["selettori"]
    for el in soup.select(s["elemento"]):
        def testo(chiave):
            n = el.select_one(s[chiave]) if s.get(chiave) else None
            return n.get_text(" ", strip=True) if n else None
        a = el.select_one(s["link"]) if s.get("link") else None
        yield {"nome": testo("nome"), "comune": testo("comune"), "data": testo("data"),
               "categoria": testo("categoria"), "descrizione": testo("descrizione"),
               "url": urljoin(src["url"], a["href"]) if a is not None and a.get("href") else None}

FETCH = {"ical": da_ical, "json": da_json, "csv": da_csv, "html": da_html}

def normalizza(g, src):
    nome = (g.get("nome") or "").strip()
    comune = (g.get("comune") or src.get("comune") or "").strip()
    if not nome or not comune:
        return None
    if g.get("data"):
        r = trova_date(g["data"])
        ini, fi = r if r else (None, None)
    else:
        ini = data_da(g.get("inizio"))
        fi = data_da(g.get("fine")) or ini
    if not ini or not fi or fi < ini:
        return None
    desc = (g.get("descrizione") or "").strip() or None
    if src.get("filtro") and not any(norm(f) in norm(nome + " " + (desc or "")) for f in src["filtro"]):
        return None
    return {"nome": nome, "comune": comune, "prov_src": (g.get("provincia") or "").strip(),
            "inizio": ini, "fine": fi, "descrizione": desc[:400] if desc else None,
            "url": (g.get("url") or "").strip() or None,
            "categoria": (g.get("categoria") or "").strip() or categoria_da(nome + " " + (desc or ""))}

def leggi_manuali():
    if not MANUALI.exists():
        return []
    campi = {k: k for k in "nome comune provincia inizio fine categoria descrizione url".split()}
    testo = MANUALI.read_text(encoding="utf-8-sig")
    return [e for e in (normalizza(g, {"nome": "manuale"}) for g in righe_csv(testo, {"campi": campi})) if e]

# ---------- deduplica ----------
def stesso_evento(a, b):
    return (a["inizio"] <= b["fine"] and b["inizio"] <= a["fine"]
            and SequenceMatcher(None, norm(a["nome"]), norm(b["nome"])).ratio() > 0.8)

def deduplica(eventi):
    gruppi = defaultdict(list)
    for e in eventi:   # i manuali arrivano per primi e hanno la precedenza
        g = gruppi[norm(e["comune"])]
        for x in g:
            if stesso_evento(x, e):
                for k in ("descrizione", "url", "prov_src"):
                    x[k] = x[k] or e[k]
                break
        else:
            g.append(e)
    return [e for g in gruppi.values() for e in g]

# ---------- geocodifica (Nominatim, con cache) ----------
_ultima = 0.0

def pulisci_prov(s):
    return re.sub(r"^(Provincia|Citt[aà] metropolitana|Libero consorzio comunale) (di|del|della|dell'|d')\s*", "", s or "").strip()

def geocodifica(comune, prov, cache, budget):
    global _ultima
    chiave = norm(f"{comune} {prov}")
    if chiave in cache:
        return cache[chiave]
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    time.sleep(max(0, 1.1 - (time.time() - _ultima)))   # max 1 richiesta al secondo
    _ultima = time.time()
    q = f"{comune}, {prov}, Italia" if prov else f"{comune}, Italia"
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", headers={"User-Agent": UA}, timeout=20,
                         params={"q": q, "format": "jsonv2", "countrycodes": "it", "addressdetails": 1, "limit": 1})
        r.raise_for_status()
        d = r.json()
    except Exception as ex:
        print(f"[geocoding] {q}: {ex}")
        return None   # errore di rete: non memorizzo, riprovo al prossimo giro
    if not d:
        cache[chiave] = None
        return None
    a = d[0].get("address", {})
    cache[chiave] = {"lat": float(d[0]["lat"]), "lon": float(d[0]["lon"]),
                     "provincia": pulisci_prov(a.get("county") or a.get("province") or ""),
                     "sigla": (a.get("ISO3166-2-lvl6") or "").replace("IT-", ""),
                     "regione": a.get("state") or ""}
    return cache[chiave]

# ---------- pipeline ----------
def main():
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8")) or {}
    eventi = leggi_manuali()
    print(f"[manuali] {len(eventi)} eventi")
    for src in cfg.get("sorgenti") or []:
        n = 0
        try:
            for g in FETCH[src["tipo"]](src):
                e = normalizza(g, src)
                if e:
                    eventi.append(e)
                    n += 1
        except Exception as ex:
            print(f"[ERRORE] {src.get('nome')}: {ex}")
        print(f"[{src.get('nome')}] {n} eventi")

    oggi = date.today()
    eventi = [e for e in eventi if e["fine"] >= oggi and e["inizio"] <= oggi + timedelta(days=400)]
    eventi = deduplica(eventi)

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    budget, out = [MAX_NUOVI_GEOCODE], []
    for e in eventi:
        geo = geocodifica(e["comune"], e["prov_src"], cache, budget)
        if not geo or not (ITALIA[0] <= geo["lat"] <= ITALIA[1] and ITALIA[2] <= geo["lon"] <= ITALIA[3]):
            print(f"[scartato] comune non localizzato: {e['comune']} ({e['nome']})")
            continue
        out.append({"id": f"{slug(e['comune'])}-{slug(e['nome'])}-{e['inizio'].isoformat()}",
                    "nome": e["nome"], "comune": e["comune"],
                    "provincia": geo["provincia"] or e["prov_src"], "sigla": geo["sigla"],
                    "regione": geo["regione"], "latitudine": round(geo["lat"], 5),
                    "longitudine": round(geo["lon"], 5), "inizio": e["inizio"].isoformat(),
                    "fine": e["fine"].isoformat(), "categoria": e["categoria"],
                    "descrizione": e["descrizione"], "url": e["url"]})
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    out.sort(key=lambda x: (x["inizio"], x["nome"]))

    if not out:
        sys.exit("Nessun evento valido: non sovrascrivo il feed.")
    nuovo = {"versione": 1, "sagre": out}
    if OUT.exists():
        try:
            vecchio = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            vecchio = {}
        n_old = len(vecchio.get("sagre", []))
        if n_old >= 20 and len(out) < n_old * 0.5 and not os.environ.get("FORZA"):
            sys.exit(f"Calo anomalo ({n_old} -> {len(out)}): una fonte e' forse rotta. FORZA=1 per ignorare.")
        if vecchio == nuovo:
            print("Nessuna modifica.")
            return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(nuovo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Scritti {len(out)} eventi in {OUT}")

if __name__ == "__main__":
    main()
