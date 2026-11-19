"""ems_tablet - tabletweergave van het HEMS-dashboard (/tablet).

Een donkere pagina voor een tablet aan de muur of een oud scherm: live
energiestroom (net, en zon/batterij/auto als die gekoppeld zijn), het lopende
kwartier tegenover het budget van het capaciteitstarief en de pieken van de
maand, de marktprijzen van vandaag en morgen, afname, teruglevering en kosten
(met vergelijking met gisteren), weer en zonprognose, en het vermogen van
vandaag.

Toestelkaarten: elk ander programma (bv. een sturingsmodule die de AI bouwt)
kan een kaart tonen door een JSON-bestand te schrijven in data/tablet/
(zie KAART_UITLEG hieronder en LEESMIJ.md). Het meetsysteem zelf leest die
bestanden alleen en stuurt nooit een toestel aan.

Weer: Open-Meteo (gratis, zonder account), locatie uit de gemeente bij
Instellingen of [tablet] breedte/lengte in ems.ini. Uit te zetten met
[tablet] weer = nee. Het dashboard haalt het weer op (hoogstens om de 30 min),
niet de tablet zelf.
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import ems_common as ec

KAART_UITLEG = """\
Een kaart = één bestand data/tablet/<naam>.json (UTF-8, hoogstens 20 kB), bv.:
{
  "titel": "Auto",                 verplicht
  "soort": "auto",                 auto | batterij | zon | laadpaal | warmtepomp | boiler | stekker | ander
  "volgorde": 10,                  kleiner = eerder (standaard 50)
  "status": "ok",                  ok | let | fout | uit | info   (kleur van het bolletje)
  "statustekst": "laadt",          korte toestand naast het bolletje
  "waarde": "72", "eenheid": "%", "label": "batterij",     het grote getal
  "balk": 72,                      0..100, optioneel (laadtoestand)
  "regels": [["Bereik", "288 km"], ["Laadstroom", "16 A"]],
  "stroom_w": -3680,               optioneel: toont het toestel in het stroomschema
                                   (+ = levert aan het huis, - = neemt van het huis)
  "foto": "mijn-auto.png",          optioneel: afbeelding (jpg/png/webp) in dezelfde map; zonder dit
                                   toont de kaart de foto die bij Instellingen > Dashboard voor deze soort
                                   opgeladen is. Een PNG met doorzichtige achtergrond (uitgeknipt) staat
                                   zonder vak op de kaart.
  "bijgewerkt": "2026-11-19T10:00:00Z",   UTC; zonder dit telt de wijzigingstijd van het bestand
  "geldig_s": 300                  daarna toont de tablet 'verouderd' (standaard 300 s)
}
Schrijf eerst naar een tijdelijk bestand en hernoem het dan (os.replace), zodat de
tablet nooit een half bestand leest.
"""

SOORTEN = ("auto", "batterij", "zon", "laadpaal", "warmtepomp", "boiler", "stekker", "ander")
_WEER = {"sleutel": None, "tijd": 0.0, "data": None, "fout": ""}
_WEER_SLOT = threading.Lock()
_LOCATIE = {}
FOTO_MAX = 3_000_000
_FOTO_NAAM = re.compile(r"^[A-Za-z0-9_-]{1,60}\.(jpg|jpeg|png|webp)$")
FOTO_TYPE = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


# ---------- toestelkaarten ----------

def kaartmap(cfgpad):
    return ec.pad_in_project(cfgpad, "data/tablet")


def _tekst(x, n=80):
    return str(x)[:n] if x is not None else ""


def lees_kaarten(cfgpad):
    """Alle geldige kaarten uit data/tablet/*.json, gesorteerd; fouten worden zelf een kaart."""
    map_ = kaartmap(cfgpad)
    uit = []
    try:
        namen = sorted(f for f in os.listdir(map_) if f.endswith(".json"))[:24]
    except OSError:
        return uit
    nu = time.time()
    for naam in namen:
        pad = os.path.join(map_, naam)
        try:
            if os.path.getsize(pad) > 20000:
                raise ValueError("bestand groter dan 20 kB")
            with open(pad, encoding="utf-8") as f:
                k = json.load(f)
            if not isinstance(k, dict) or not k.get("titel"):
                raise ValueError("geen 'titel'")
            mtime = os.path.getmtime(pad)
        except Exception as e:
            uit.append({"id": naam[:-5], "titel": naam, "soort": "ander", "status": "fout",
                        "statustekst": "onleesbaar", "regels": [["Fout", _tekst(e, 120)]], "volgorde": 999,
                        "waarde": "", "eenheid": "", "label": "", "leeftijd_s": 0, "verouderd": False})
            continue
        bijgewerkt = mtime
        if k.get("bijgewerkt"):
            try:
                bijgewerkt = ec.parse_iso(str(k["bijgewerkt"])).timestamp()
            except Exception:
                pass
        try:
            geldig = float(k.get("geldig_s", 300))
        except (TypeError, ValueError):
            geldig = 300
        volgorde = k.get("volgorde", 50)
        kaart = {
            "id": naam[:-5],
            "titel": _tekst(k["titel"], 40),
            "soort": k.get("soort") if k.get("soort") in SOORTEN else "ander",
            "volgorde": volgorde if isinstance(volgorde, (int, float)) else 50,
            "status": k.get("status") if k.get("status") in ("ok", "let", "fout", "uit", "info") else "info",
            "statustekst": _tekst(k.get("statustekst"), 60),
            "waarde": _tekst(k.get("waarde"), 16),
            "eenheid": _tekst(k.get("eenheid"), 12),
            "label": _tekst(k.get("label"), 40),
            "regels": [[_tekst(r[0], 40), _tekst(r[1], 60)] for r in (k.get("regels") or [])[:8]
                       if isinstance(r, (list, tuple)) and len(r) == 2],
            "leeftijd_s": round(nu - bijgewerkt),
            "verouderd": nu - bijgewerkt > geldig,
        }
        if isinstance(k.get("balk"), (int, float)):
            kaart["balk"] = max(0.0, min(100.0, float(k["balk"])))
        if isinstance(k.get("stroom_w"), (int, float)) and not kaart["verouderd"]:
            kaart["stroom_w"] = float(k["stroom_w"])
        f = foto_url(cfgpad, str(k.get("foto") or ""), kaart["soort"])
        if f:
            kaart["foto"] = f
        uit.append(kaart)
    uit.sort(key=lambda k: (k["volgorde"], k["titel"]))
    return uit


def plaatshouders(cfgpad, kaarten):
    """Toestellen die in de vragenlijst staan maar (nog) geen kaart hebben."""
    try:
        import ems_vragenlijst as ev
        w = ev.waarden(cfgpad)
    except Exception:
        return []
    soorten = {k["soort"] for k in kaarten}
    if "laadpaal" in soorten:
        soorten.add("auto")
    uit = []
    for sectie, soort, titel in (("vl_zon", "zon", "Zonnepanelen"), ("vl_batterij", "batterij", "Thuisbatterij"),
                                 ("vl_auto", "auto", "Elektrische auto")):
        if (w.get(sectie) or {}).get("heeft") == "ja" and soort not in soorten:
            p = {"soort": soort, "titel": titel}
            f = foto_url(cfgpad, "", soort)
            if f:
                p["foto"] = f
            uit.append(p)
    return uit


# ---------- foto's ----------

def foto_url(cfgpad, naam, soort):
    """URL van de foto van een kaart: eigen bestand uit de kaart, anders foto-<soort>.jpg; '' als er geen is."""
    for n in ([naam] if naam else []) + [f"foto-{soort}.png", f"foto-{soort}.jpg"]:
        if _FOTO_NAAM.match(n):
            pad = os.path.join(kaartmap(cfgpad), n)
            if os.path.isfile(pad):
                return f"/tablet/foto/{n}?v={int(os.path.getmtime(pad))}"
    return ""


def foto_bestand(cfgpad, naam):
    """(pad, content-type) van een foto in data/tablet, of None (enkel veilige namen)."""
    m = _FOTO_NAAM.match(naam or "")
    if not m:
        return None
    pad = os.path.join(kaartmap(cfgpad), naam)
    return (pad, FOTO_TYPE[m.group(1).lower()]) if os.path.isfile(pad) else None


def fotos(cfgpad):
    """{soort: versie} van de foto's die bij Instellingen opgeladen zijn."""
    uit = {}
    for soort in SOORTEN:
        for ext in ("png", "jpg"):
            pad = os.path.join(kaartmap(cfgpad), f"foto-{soort}.{ext}")
            if os.path.isfile(pad):
                uit[soort] = f"foto-{soort}.{ext}?v={int(os.path.getmtime(pad))}"
                break
    return uit


def bewaar_foto(cfgpad, soort, data):
    if soort not in SOORTEN:
        return {"ok": False, "fout": "Onbekend toestel."}
    if not data or len(data) > FOTO_MAX:
        return {"ok": False, "fout": "De foto is leeg of groter dan 3 MB."}
    if data.startswith(b"\xff\xd8\xff"):
        ext, ander = "jpg", "png"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        ext, ander = "png", "jpg"          # PNG met doorzichtige achtergrond = uitgeknipte foto
    else:
        return {"ok": False, "fout": "Dat is geen JPEG- of PNG-foto."}
    map_ = kaartmap(cfgpad)
    os.makedirs(map_, exist_ok=True)
    pad = os.path.join(map_, f"foto-{soort}.{ext}")
    tmp = pad + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, pad)
    try:
        os.remove(os.path.join(map_, f"foto-{soort}.{ander}"))
    except FileNotFoundError:
        pass
    return {"ok": True, "bericht": "Foto bewaard. De tablet toont ze binnen 10 seconden.", "fotos": fotos(cfgpad)}


def verwijder_foto(cfgpad, soort):
    if soort not in SOORTEN:
        return {"ok": False, "fout": "Onbekend toestel."}
    for ext in ("jpg", "png"):
        try:
            os.remove(os.path.join(kaartmap(cfgpad), f"foto-{soort}.{ext}"))
        except FileNotFoundError:
            pass
    return {"ok": True, "bericht": "Foto verwijderd.", "fotos": fotos(cfgpad)}


# ---------- weer (Open-Meteo) ----------

def _haal_json(url, timeout=8):
    verzoek = urllib.request.Request(url, headers={"User-Agent": "hems-meting/1.3"})
    with urllib.request.urlopen(verzoek, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def locatie(cfg):
    """(breedte, lengte, naam) uit [tablet] of uit de gemeente bij Instellingen; None als onbekend."""
    try:
        b = ec.cfg_str(cfg, "tablet", "breedte")
        l = ec.cfg_str(cfg, "tablet", "lengte")
        if b and l:
            return float(b.replace(",", ".")), float(l.replace(",", ".")), ec.cfg_str(cfg, "tarief", "gemeente")
    except ValueError:
        pass
    gemeente = ec.cfg_str(cfg, "tarief", "gemeente")
    land = ec.cfg_str(cfg, "tarief", "land", "BE")
    if not gemeente:
        return None
    sleutel = (gemeente, land)
    if sleutel not in _LOCATIE:
        # fusiegemeenten zoals "Nazareth-De Pinte": probeer ook het eerste deel
        pogingen = [gemeente] + ([gemeente.split("-")[0]] if "-" in gemeente else [])
        gevonden = None
        for naam in pogingen:
            q = urllib.parse.urlencode({"name": naam, "count": 1, "language": "nl", "format": "json",
                                        **({"countryCode": land} if land in ("BE", "NL") else {})})
            d = _haal_json("https://geocoding-api.open-meteo.com/v1/search?" + q)
            if d.get("results"):
                r = d["results"][0]
                gevonden = (round(r["latitude"], 3), round(r["longitude"], 3), gemeente)
                break
        _LOCATIE[sleutel] = gevonden
    return _LOCATIE[sleutel]


def weer(cfg, tz, kwp=None):
    """Weer van vandaag + 2 dagen en de zoninstraling per uur; gecachet 30 min."""
    if not ec.cfg_bool(cfg, "tablet", "weer", True):
        return {"uit": True}
    with _WEER_SLOT:
        try:
            loc = locatie(cfg)
        except Exception as e:
            return {"fout": f"Locatie opzoeken mislukt ({e})."}
        if not loc:
            return {"fout": "Vul je gemeente in bij Instellingen > Contract en tarief, dan verschijnt hier het weer."}
        sleutel = (loc, str(tz))
        if _WEER["sleutel"] == sleutel and time.time() - _WEER["tijd"] < 1800 and _WEER["data"]:
            d = _WEER["data"]
        elif _WEER["sleutel"] == sleutel and time.time() - _WEER["tijd"] < 300 and _WEER["fout"]:
            return {"fout": _WEER["fout"]}
        else:
            q = urllib.parse.urlencode({
                "latitude": loc[0], "longitude": loc[1], "timezone": str(tz), "forecast_days": 3,
                "current": "temperature_2m,weather_code,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,sunshine_duration",
                "hourly": "shortwave_radiation",
            })
            try:
                d = _haal_json("https://api.open-meteo.com/v1/forecast?" + q)
                _WEER.update(sleutel=sleutel, tijd=time.time(), data=d, fout="")
            except Exception as e:
                _WEER.update(sleutel=sleutel, tijd=time.time(), data=None, fout=f"Weer ophalen mislukt ({e}).")
                return {"fout": _WEER["fout"]}
    dag = d.get("daily") or {}
    uur = d.get("hourly") or {}
    straling = {}
    for t, w in zip(uur.get("time") or [], uur.get("shortwave_radiation") or []):
        straling.setdefault(t[:10], []).append(w or 0)
    dagen = []
    for i, datum in enumerate(dag.get("time") or []):
        r = straling.get(datum, [])
        dagen.append({
            "datum": datum, "code": dag["weather_code"][i], "max": dag["temperature_2m_max"][i],
            "min": dag["temperature_2m_min"][i], "neerslag": dag["precipitation_sum"][i],
            "zon_u": round((dag["sunshine_duration"][i] or 0) / 3600, 1), "straling": r,
            # ruwe schatting: kWh/m2 op een horizontaal vlak x kWp x 0,8 (verliezen, gemiddelde oriëntatie)
            "zon_kwh": round(sum(r) / 1000 * kwp * 0.8, 1) if kwp and r else None,
        })
    cur = d.get("current") or {}
    return {"plaats": loc[2], "nu": {"temp": cur.get("temperature_2m"), "code": cur.get("weather_code"),
                                     "dag": cur.get("is_day", 1)},
            "dagen": dagen, "kwp": kwp}


def _kwp(cfgpad):
    try:
        import ems_vragenlijst as ev
        z = ev.waarden(cfgpad).get("vl_zon") or {}
        if z.get("heeft") == "ja" and z.get("kwp"):
            return float(str(z["kwp"]).replace(",", "."))
    except Exception:
        pass
    return None


# ---------- pieken en gisteren ----------

def pieken_maand(conn, tz, aantal=5):
    nu = ec.utc_nu().astimezone(tz)
    start = datetime(nu.year, nu.month, 1, tzinfo=tz).astimezone(timezone.utc)
    rows = conn.execute(
        "SELECT einde_utc, gemiddeld_w FROM kwartieren WHERE einde_utc>? AND compleet=1"
        " ORDER BY gemiddeld_w DESC LIMIT ?", (ec.iso(start), aantal)).fetchall()
    uit = []
    for einde, w in rows:
        e = ec.parse_iso(einde).astimezone(tz)
        b = e - timedelta(minutes=15)
        uit.append({"tekst": f"{b.strftime('%d/%m %H:%M')}-{e.strftime('%H:%M')}", "w": w})
    return uit


def _teller(conn, ts):
    return conn.execute("SELECT import_kwh, export_kwh FROM metingen WHERE ts<=? AND import_kwh IS NOT NULL"
                        " ORDER BY ts DESC LIMIT 1", (ec.iso(ts),)).fetchone()


def _verschil(a, b):
    if not a or not b or a[1] is None or b[1] is None:
        return None, None
    di, de = b[0] - a[0], b[1] - a[1]
    if not 0 <= di < 200 or not 0 <= de < 200:
        return None, None
    return round(di, 2), round(de, 2)


def _stand(conn, kolom, ts):
    """Laatste niet-lege meterstand (gas_m3 of water_m3) op of voor ts."""
    r = conn.execute(f"SELECT {kolom} FROM metingen WHERE ts<=? AND {kolom} IS NOT NULL ORDER BY ts DESC LIMIT 1",
                     (ec.iso(ts),)).fetchone()
    return r[0] if r else None


def gas_water(conn, tz, cfg=None):
    """Gas (m3) en water (liter) van vandaag tot nu en van gisteren tot hetzelfde uur, met kosten als het
    tarief bij Instellingen > Gas en water ingevuld is. Geen meter = geen sleutel."""
    import ems_kosten
    t = ems_kosten.gaswater(cfg) if cfg is not None else None
    nu = ec.utc_nu()
    vandaag = nu.astimezone(tz).date()
    m_start, _ = ec.dag_grenzen_utc(vandaag.replace(day=1), tz)
    v_start, _ = ec.dag_grenzen_utc(vandaag, tz)
    g_start, _ = ec.dag_grenzen_utc(vandaag - timedelta(days=1), tz)
    zelfde = g_start + (nu - v_start)
    uit = {}
    for kolom, naam, factor in (("gas_m3", "gas", 1), ("water_m3", "water", 1000)):
        laatste = conn.execute(f"SELECT MAX(ts) FROM metingen WHERE {kolom} IS NOT NULL").fetchone()[0]
        if not laatste or (nu - ec.parse_iso(laatste)).total_seconds() > 2 * 86400:
            continue                     # geen (werkende) meter: geen tegel
        a, b = _stand(conn, kolom, v_start), _stand(conn, kolom, nu)
        c, d = _stand(conn, kolom, g_start), _stand(conn, kolom, zelfde)
        def verschil(x, y, maximum=100):     # m3; groter = meter vervangen of teller verkeerd
            return round((y - x) * factor, 3) if x is not None and y is not None and 0 <= y - x < maximum else None
        r = {"vandaag": verschil(a, b), "gisteren_tot_nu": verschil(c, d), "stand": b}
        maand = verschil(_stand(conn, kolom, m_start), b, 3000)
        if t is not None:
            kost = ems_kosten.kosten_gas if naam == "gas" else ems_kosten.kosten_water
            m3 = lambda x: None if x is None else x / factor
            r["kosten_vandaag"] = kost(m3(r["vandaag"]), 1, t)
            r["kosten_maand"] = kost(m3(maand), vandaag.day, t)
        uit[naam] = r
    return uit


def gisteren(conn, tz, cfg):
    """Afname/teruglevering van gisteren tot hetzelfde uur, en gisteren in totaal (met kosten)."""
    import ems_kosten
    nu = ec.utc_nu()
    vandaag = nu.astimezone(tz).date()
    g = vandaag - timedelta(days=1)
    g_start, g_einde = ec.dag_grenzen_utc(g, tz)
    v_start, _ = ec.dag_grenzen_utc(vandaag, tz)
    zelfde = g_start + (nu - v_start)
    a = _teller(conn, g_start)
    # geen meting vlak voor middernacht = gisteren niet (volledig) gemeten
    if not a or (g_start - ec.parse_iso(conn.execute(
            "SELECT MAX(ts) FROM metingen WHERE ts<=?", (ec.iso(g_start),)).fetchone()[0])).total_seconds() > 900:
        return {}
    imp_tot_nu, exp_tot_nu = _verschil(a, _teller(conn, zelfde))
    imp, exp = _verschil(a, _teller(conn, g_einde))
    try:
        kosten = ems_kosten.kosten_dag(conn, g, tz, cfg)["totaal_eur"]
    except Exception:
        kosten = None
    return {"afname_tot_nu_kwh": imp_tot_nu, "terug_tot_nu_kwh": exp_tot_nu,
            "afname_kwh": imp, "terug_kwh": exp, "kosten_eur": kosten,
            "zelfde_uur": zelfde.astimezone(tz).strftime("%H:%M")}


# ---------- samenstellen ----------

def _prijzen(conn, van_utc, tot_utc):
    rows = conn.execute(
        "SELECT start_utc, eur_kwh FROM prijzen WHERE start_utc>=? AND start_utc<? ORDER BY start_utc",
        (ec.iso(van_utc), ec.iso(tot_utc))).fetchall()
    return [[int(ec.parse_iso(ts).timestamp() * 1000), round(p * 100, 3)] for ts, p in rows if p is not None]


def _vermogen_per_5min(conn, start, einde):
    """Gemiddeld vermogen per 5 minuten: [[epoch ms, W], ...]."""
    rows = conn.execute(
        "SELECT ts, vermogen_w FROM metingen WHERE ts>=? AND ts<? AND vermogen_w IS NOT NULL ORDER BY ts",
        (ec.iso(start), ec.iso(einde))).fetchall()
    emmers = {}
    for ts, v in rows:
        t = int(ec.parse_iso(ts).timestamp())
        emmers.setdefault(t - t % 300, []).append(v)
    return [[k * 1000, round(sum(v) / len(v))] for k, v in sorted(emmers.items())]


def _bron(cfg):
    url = ec.cfg_str(cfg, "provider", "api_url")
    if not url:
        return ""
    host = urlparse(url).netloc
    if "entsoe" in host:
        return "ENTSO-E"
    if "energyzero" in host:
        return "EnergyZero"
    return host


def kaarten_data(cfgpad):
    k = lees_kaarten(cfgpad)
    return {"kaarten": k, "plaatshouders": plaatshouders(cfgpad, k)}


def data(conn, cfg, cfgpad, tz, maand_tot_nu, tarief_ingevuld):
    """Alles wat niet elke 5 s moet: totalen, prijzen, grafiek, pieken, gisteren, weer, kaarten."""
    nu = ec.utc_nu()
    vandaag = nu.astimezone(tz).date()
    start, einde = ec.dag_grenzen_utc(vandaag, tz)
    _, einde_morgen = ec.dag_grenzen_utc(vandaag + timedelta(days=1), tz)
    t = ec.dag_totalen(conn, vandaag, tz, cfg)
    kw = conn.execute(
        "SELECT MAX(gemiddeld_w) FROM kwartieren WHERE einde_utc>? AND einde_utc<=? AND compleet=1",
        (ec.iso(start), ec.iso(einde))).fetchone()
    try:
        w = weer(cfg, tz, _kwp(cfgpad))
    except Exception as e:
        w = {"fout": f"Weer: {e}"}
    try:
        g = gisteren(conn, tz, cfg)
    except Exception:
        g = {}
    return {
        "nu_ms": int(nu.timestamp() * 1000),
        "dag_start_ms": int(start.timestamp() * 1000),
        "dag_einde_ms": int(einde.timestamp() * 1000),
        "morgen_einde_ms": int(einde_morgen.timestamp() * 1000),
        "tijdzone": str(tz),
        "demo": ec.demo_actief(cfg),
        "fasen": ec.cfg_int(cfg, "installatie", "fasen", 3),
        "hoofdzekering_a": ec.cfg_float(cfg, "installatie", "hoofdzekering_a", 25),
        "minimum_piek_w": ec.cfg_float(cfg, "installatie", "minimum_piek_kw", 2.5) * 1000,
        "vandaag": {
            "afname_kwh": round(t["import_dag_kwh"] + t["import_nacht_kwh"], 2),
            "afname_dag_kwh": t["import_dag_kwh"],
            "afname_nacht_kwh": t["import_nacht_kwh"],
            "teruglevering_kwh": t["export_kwh"],
            "kosten_eur": t["kosten_eur"],
            "maand_eur": maand_tot_nu(vandaag),
            "hoogste_kwartier_w": kw[0] if kw and kw[0] is not None else None,
        },
        "gisteren": g,
        "gas_water": gas_water(conn, tz, cfg),
        "pieken": pieken_maand(conn, tz),
        "tarief_ingevuld": tarief_ingevuld,
        "prijzen": _prijzen(conn, start, einde_morgen),
        "prijsbron": _bron(cfg),
        "vermogen": _vermogen_per_5min(conn, start, einde),
        "weer": w,
        **kaarten_data(cfgpad),
    }


# ---------- pagina ----------

PAGINA = r"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<title>HEMS tablet</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0d141c">
<style>
:root{
 --achter:#0d141c;--kaart:#16212c;--kaart2:#1c2935;--rand:#27384a;--tekst:#e8eef4;--zacht:#93a4b5;
 --afname:#E69F00;--terug:#56B4E9;--zon:#F0C419;--bat:#2fbf8f;--auto:#b48cf2;
 --goed:#2fbf8f;--let:#E69F00;--slecht:#e5654b;
 --r:18px;--gap:clamp(8px,1.4vw,14px)}
*{box-sizing:border-box}
html,body{margin:0;background:var(--achter);color:var(--tekst);
 font-family:system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif;-webkit-font-smoothing:antialiased}
body{padding:var(--gap);min-height:100vh;display:flex;flex-direction:column;gap:var(--gap)}
a{color:var(--terug)}
.kop{display:flex;align-items:center;gap:14px;background:var(--kaart);border:1px solid var(--rand);
 border-radius:var(--r);padding:10px 16px}
.logo{width:40px;height:40px;border-radius:11px;background:linear-gradient(140deg,#2fbf8f,#1f7f9f);
 display:grid;place-items:center;flex:none}
.titel b{font-size:1.25em;display:block;letter-spacing:.2px}
.titel span{color:var(--zacht);font-size:.8em}
.kop .rechts{margin-left:auto;display:flex;align-items:center;gap:14px;text-align:right}
.weerkop{display:flex;gap:12px;align-items:center;font-size:.75em;color:var(--zacht)}
.weerkop .d{text-align:center;line-height:1.15}
.weerkop .d b{color:var(--tekst);font-weight:600}
.weerkop svg{width:24px;height:24px;display:block;margin:1px auto}
.klok{font-size:1.6em;font-weight:650;font-variant-numeric:tabular-nums;line-height:1}
.datum{color:var(--zacht);font-size:.8em}
.live{font-size:.75em;font-weight:700;color:var(--goed);display:flex;align-items:center;gap:6px}
.live i{width:9px;height:9px;border-radius:50%;background:currentColor;animation:puls 2s infinite}
.live.stil{color:var(--slecht)}
@keyframes puls{50%{opacity:.35}}
.knop{background:var(--kaart2);border:1px solid var(--rand);color:var(--tekst);border-radius:10px;
 width:38px;height:38px;display:grid;place-items:center;cursor:pointer;text-decoration:none;font-size:18px}
.demo{background:#5a2a14;border:1px solid #a2512a;border-radius:12px;padding:8px 14px;font-size:.9em;display:none}
.rooster{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--gap);grid-auto-flow:row dense}
.kaart{background:var(--kaart);border:1px solid var(--rand);border-radius:var(--r);padding:14px 16px;
 display:flex;flex-direction:column;min-width:0}
.breed{grid-column:1/-1}
.kaart h2{margin:0 0 10px;font-size:.82em;font-weight:650;color:var(--zacht);text-transform:uppercase;
 letter-spacing:.7px;display:flex;align-items:center;gap:8px}
.kaart h2 .extra{margin-left:auto;text-transform:none;letter-spacing:0;font-weight:500}
.groot{font-size:clamp(1.9em,4.6vw,2.6em);font-weight:700;font-variant-numeric:tabular-nums;line-height:1.05}
.klein{color:var(--zacht);font-size:.85em}
.afname{color:var(--afname)}.terug{color:var(--terug)}
/* energiestroom */
.stroom{position:relative;flex:1;display:flex;align-items:center}
.stroom svg{width:100%;height:auto;display:block;max-height:320px}
.knoop{fill:var(--kaart2);stroke:var(--rand);stroke-width:1.5}
.knoopnaam{fill:var(--zacht);font-size:10.5px;text-anchor:middle}
.knoopw{fill:var(--tekst);font-size:11.5px;font-weight:650;text-anchor:middle}
.lijn{fill:none;stroke-width:4.5;stroke-linecap:round;stroke-dasharray:2 13}
.lijnbasis{fill:none;stroke:var(--rand);stroke-width:4.5;stroke-linecap:round}
.fasen{display:grid;gap:7px;margin-top:8px}
.fase{display:grid;grid-template-columns:28px 1fr 74px;align-items:center;gap:8px;font-size:.85em}
.fase .balk{height:10px;background:var(--kaart2);border-radius:5px;overflow:hidden;position:relative}
.fase .balk div{position:absolute;top:0;bottom:0;border-radius:5px}
.fase .w{text-align:right;font-variant-numeric:tabular-nums}
/* meter (capaciteitstarief) */
.meter{display:flex;flex-direction:column;align-items:center;flex:1}
.meter svg{width:min(100%,230px);height:auto}
.meter .waarde{margin-top:-54px;text-align:center}
.regels{display:grid;grid-template-columns:1fr auto;gap:4px 10px;font-size:.88em;margin-top:12px;width:100%}
.regels span:nth-child(even){text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.pieken{width:100%;margin-top:12px;display:grid;gap:5px;font-size:.8em}
.piek{display:grid;grid-template-columns:108px 1fr 52px;gap:8px;align-items:center}
.piek .balk{height:8px;background:var(--kaart2);border-radius:4px;position:relative}
.piek .balk div{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:#6f8396}
.piek .balk i{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--zacht)}
.piek span:last-child{text-align:right;font-variant-numeric:tabular-nums}
/* prijzen */
.prijskop{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:8px}
.grafiek{width:100%;height:150px;display:block}
.groei .grafiek{flex:1;height:auto;min-height:130px}
.as{position:relative;height:16px;font-size:.75em;color:var(--zacht);margin-top:3px}
.as span{position:absolute;top:0;transform:translateX(-50%);white-space:nowrap}
.as span.l{transform:none}.as span.r{transform:translateX(-100%)}
.legende{display:flex;gap:14px;flex-wrap:wrap;font-size:.78em;color:var(--zacht);margin-top:6px}
.legende i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
/* tegels vandaag */
.tegels{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--gap)}
.tegels.zes{grid-template-columns:repeat(3,minmax(0,1fr))}
.tegel{background:var(--kaart2);border:1px solid var(--rand);border-radius:14px;padding:10px 12px;min-width:0}
.tegel .label{font-size:.78em;color:var(--zacht)}
.tegel .getal{font-size:clamp(1.3em,3vw,1.75em);font-weight:700;font-variant-numeric:tabular-nums;margin:2px 0}
.tegel .sub{font-size:.75em;color:var(--zacht)}
.tegel .verg{font-size:.75em;margin-top:4px;color:var(--zacht)}
.op{color:var(--slecht)}.neer{color:var(--goed)}
/* toestelkaarten */
.toestel{gap:6px}
.toestel .bovenaan{display:flex;align-items:center;gap:10px}
.toestel .icoon{width:38px;height:38px;border-radius:11px;display:grid;place-items:center;flex:none;background:var(--kaart2)}
.toestel .icoon svg{width:24px;height:24px}
.toestel .naam{font-weight:650}
.toestel .stat{font-size:.8em;color:var(--zacht);display:flex;align-items:center;gap:6px}
.toestel .stat i{width:8px;height:8px;border-radius:50%;background:var(--zacht);flex:none}
.st-ok i{background:var(--goed)!important}.st-let i{background:var(--let)!important}
.st-fout i{background:var(--slecht)!important}.st-uit i{background:#55636f!important}
.toestel .getal{font-size:clamp(1.6em,4vw,2.1em);font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.toestel .getal small{font-size:.5em;color:var(--zacht);font-weight:500;margin-left:4px}
.toestel .laad{height:9px;background:var(--kaart2);border-radius:5px;overflow:hidden}
.toestel .laad div{height:100%;border-radius:5px}
.toestel .regels{margin-top:4px;font-size:.84em}
.toestel.oud{opacity:.6}
.toestel .foto{margin:2px -4px 0;border-radius:12px;overflow:hidden;background:var(--kaart2);aspect-ratio:4/3;max-height:320px}
.toestel .foto img{width:100%;height:100%;object-fit:cover;display:block}
.toestel .foto.vrij{aspect-ratio:auto;background:none;border-radius:0;margin:4px 0 0;max-height:none}
.toestel .foto.vrij img{object-fit:contain;max-height:260px;filter:drop-shadow(0 10px 14px rgba(0,0,0,.45))}
.toestel .metfoto{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;align-items:end}
.toestel.leegkaart{border-style:dashed;background:transparent}
/* weer */
.dagen{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:10px}
.wdag{background:var(--kaart2);border:1px solid var(--rand);border-radius:12px;padding:8px 4px;text-align:center;font-size:.82em}
.wdag svg{width:34px;height:34px;display:block;margin:2px auto}
.wdag b{font-size:1.05em}
.voet{text-align:center;color:var(--zacht);font-size:.75em;padding:2px 0 4px}
.voet b{color:var(--goed)}
.leeg{color:var(--zacht);font-size:.9em;padding:16px 0;text-align:center}
@media (min-width:1150px){
 .rooster{grid-template-columns:repeat(4,minmax(0,1fr))}
 .rooster .half{grid-column:span 2}
}
@media (max-width:900px){.weerkop .d:nth-child(n+2){display:none}}
@media (max-width:620px){
 .rooster{grid-template-columns:1fr}
 .tegels{grid-template-columns:repeat(2,minmax(0,1fr))}
 .kop .datum,.titel span,.weerkop{display:none}
}
body.verberg{cursor:none}
</style></head>
<body>
<header class="kop">
 <div class="logo"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2"
  stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M12.5 11l-2.5 4h4l-2.5 4"/></svg></div>
 <div class="titel"><b>HEMS</b><span>energie thuis, live gemeten</span></div>
 <div class="rechts">
  <div class="weerkop" id="weerkop"></div>
  <div><div class="klok" id="klok">--:--</div><div class="datum" id="datum"></div></div>
  <div class="live" id="live"><i></i><span>LIVE</span></div>
  <button class="knop" id="volscherm" title="Volledig scherm">&#x26F6;</button>
  <a class="knop" href="/" title="Gewoon dashboard">&#x2630;</a>
 </div>
</header>
<div class="demo" id="demo"><b>DEMO-MODUS:</b> gesimuleerde metingen, niet die van je meter. Stel je meter in via Instellingen.</div>

<main class="rooster" id="rooster">
 <section class="kaart" id="k-stroom">
  <h2>Nu <span class="extra klein" id="richting"></span></h2>
  <div class="groot" id="vermogen">&hellip;</div>
  <div class="stroom"><svg id="schema" viewBox="0 0 320 200" aria-label="Energiestroom"></svg></div>
  <div class="fasen" id="fasen"></div>
 </section>

 <section class="kaart" id="k-kwartier">
  <h2>Capaciteitstarief <span class="extra klein" id="kw-einde"></span></h2>
  <div class="meter">
   <svg viewBox="0 0 200 120" aria-label="Kwartiergemiddelde tegenover budget">
    <path d="M20 105 A80 80 0 0 1 180 105" fill="none" stroke="var(--kaart2)" stroke-width="16" stroke-linecap="round"/>
    <path id="boog" d="M20 105 A80 80 0 0 1 180 105" fill="none" stroke="var(--goed)" stroke-width="16"
     stroke-linecap="round" pathLength="100" stroke-dasharray="0 100" stroke-opacity="0"/>
   </svg>
   <div class="waarde"><div class="groot" id="kw-gem">&hellip;</div><div class="klein">gemiddeld dit kwartier</div></div>
   <div class="regels">
    <span title="Hoogste van 2,5 kW en de maandpiek tot nu">Budget dit kwartier</span><span id="kw-budget">-</span>
    <span>Nog ruimte dit kwartier</span><span id="kw-rest">-</span>
    <span>Hoogste kwartier vandaag</span><span id="kw-vandaag">-</span>
   </div>
   <div class="pieken" id="pieken"></div>
  </div>
 </section>

 <section class="kaart breed half" id="k-prijzen">
  <h2>Marktprijs <span class="extra klein" id="prijsbron"></span></h2>
  <div id="prijsinhoud"><div class="leeg">Prijzen laden&hellip;</div></div>
 </section>

 <section class="kaart breed half">
  <h2>Vandaag <span class="extra klein" id="verg-uitleg"></span></h2>
  <div class="tegels">
   <div class="tegel"><div class="label">Afname</div><div class="getal afname" id="t-afname">-</div><div class="sub" id="t-afname-sub">kWh</div><div class="verg" id="g-afname"></div></div>
   <div class="tegel"><div class="label">Teruglevering</div><div class="getal terug" id="t-terug">-</div><div class="sub">kWh</div><div class="verg" id="g-terug"></div></div>
   <div class="tegel"><div class="label">Kosten vandaag</div><div class="getal" id="t-kost">-</div><div class="sub" id="t-kost-sub">alles inbegrepen</div><div class="verg" id="g-kost"></div></div>
   <div class="tegel"><div class="label">Deze maand</div><div class="getal" id="t-maand">-</div><div class="sub">tot nu</div></div>
   <div class="tegel" id="tg-gas" style="display:none"><div class="label">Gas</div><div class="getal" style="color:#e07fae" id="t-gas">-</div><div class="sub" id="t-gas-sub">m³</div><div class="verg" id="g-gas"></div></div>
   <div class="tegel" id="tg-water" style="display:none"><div class="label">Water</div><div class="getal" style="color:#7fd3e0" id="t-water">-</div><div class="sub" id="t-water-sub">liter</div><div class="verg" id="g-water"></div></div>
  </div>
 </section>

 <div id="toestellen" style="display:contents"></div>

 <section class="kaart" id="k-weer" style="display:none">
  <h2>Weer &amp; zon <span class="extra klein" id="weerplaats"></span></h2>
  <div id="weerinhoud"></div>
 </section>

 <section class="kaart breed half groei" id="k-dag">
  <h2>Vermogen vandaag <span class="extra klein"><span class="afname">&#9632;</span> afname &nbsp; <span class="terug">&#9632;</span> teruglevering</span></h2>
  <div class="leeg" id="dag-leeg" style="display:none">Nog geen metingen vandaag</div>
  <svg class="grafiek" id="dag" viewBox="0 0 1000 150" preserveAspectRatio="none" aria-label="Vermogen per 5 minuten vandaag"></svg>
  <div class="as" id="dag-as"></div>
 </section>
</main>
<div class="voet" id="voet">HEMS-meetsysteem &middot; alleen meten, geen sturing</div>

<script>
"use strict";
const $=id=>document.getElementById(id);
let TZ=__TZ__;
try{new Date().toLocaleTimeString("nl-BE",{timeZone:TZ})}catch(e){TZ=undefined}   // onbekende zone: die van het toestel
let D=null, K={kaarten:[],plaatshouders:[]}, laatsteLive=null, laatsteNet=null;
const komma=(x,d)=>x==null||isNaN(x)?"-":Number(x).toLocaleString("nl-BE",{minimumFractionDigits:d,maximumFractionDigits:d});
const kw=w=>w==null?"-":Math.abs(w)>=1000?komma(w/1000,2)+" kW":komma(Math.round(w),0)+" W";
const kwKort=w=>w==null?"-":Math.abs(w)>=1000?komma(w/1000,1)+" kW":komma(Math.round(w),0)+" W";
const euro=x=>x==null?"-":"€ "+komma(x,2);
const uur=(ms,opts)=>new Date(ms).toLocaleTimeString("nl-BE",Object.assign({hour:"2-digit",minute:"2-digit",timeZone:TZ},opts||{}));
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

/* ---------- iconen (getekend rond 0,0 in een vak van 28) ---------- */
const IC={
 net:'<path d="M-7 12 L0 -12 L7 12 M-9 -4 H9 M-6 4 H6"/>',
 huis:'<path d="M-11 2 L0 -9 L11 2 M-8 0 V11 H8 V0"/>',
 zon:'<circle r="5"/><path d="M0 -11V-8M0 8V11M-11 0H-8M8 0H11M-7.8 -7.8L-5.7 -5.7M5.7 5.7L7.8 7.8M-7.8 7.8L-5.7 5.7M5.7 -5.7L7.8 -7.8"/>',
 batterij:'<rect x="-6" y="-9" width="12" height="19" rx="2"/><path d="M-2.5 -11.5H2.5M-3 4H3M-3 0H3"/>',
 auto:'<path d="M-11 4V0L-8 -6H8L11 0V4H-11Z"/><circle cx="-6" cy="6" r="2.2"/><circle cx="6" cy="6" r="2.2"/>',
 laadpaal:'<rect x="-7" y="-10" width="10" height="20" rx="2"/><path d="M3 -3H7V5Q7 8 9 8"/><path d="M-2.5 -5L-4.5 0H-0.5L-2.5 5"/>',
 warmtepomp:'<rect x="-10" y="-8" width="20" height="16" rx="2"/><circle cx="-2" cy="0" r="5"/><path d="M-2 -5V5M-7 0H3M6 -4V4"/>',
 boiler:'<rect x="-6" y="-11" width="12" height="22" rx="5"/><path d="M-2 3Q0 -1 2 3Q2 6 0 6Q-2 6 -2 3Z"/>',
 stekker:'<path d="M-4 -11V-5M4 -11V-5M-7 -5H7V0Q7 6 0 6Q-7 6 -7 0Z M0 6V11"/>',
 ander:'<circle r="9"/><path d="M0 -4V1M0 4.5V5"/>'
};
const KLEUR={zon:"var(--zon)",batterij:"var(--bat)",auto:"var(--auto)",laadpaal:"var(--auto)",warmtepomp:"#e07fae",boiler:"#e07fae",stekker:"#9fb3c8",ander:"#9fb3c8"};
function icoon(s,kleur){return '<svg viewBox="-14 -14 28 28"><g stroke="'+(kleur||"currentColor")+'" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">'+(IC[s]||IC.ander)+"</g></svg>"}

function klok(){
  const n=new Date();
  $("klok").textContent=n.toLocaleTimeString("nl-BE",{hour:"2-digit",minute:"2-digit",timeZone:TZ});
  $("datum").textContent=n.toLocaleDateString("nl-BE",{weekday:"long",day:"numeric",month:"long",timeZone:TZ});
  if(laatsteLive){
    const s=Math.max(0,Math.round((laatsteLive.einde-Date.now())/1000));
    $("kw-einde").textContent="tot "+laatsteLive.eindeTekst+" · nog "+Math.floor(s/60)+":"+String(s%60).padStart(2,"0");
  }
}

/* ---------- stroomschema: net (en zon/batterij) links, huis in het midden, verbruikers rechts ---------- */
let LIJNEN=[], tVorig=performance.now();
function animeer(t){
  const dt=Math.min(.2,(t-tVorig)/1000); tVorig=t;
  for(const l of LIJNEN){l.pos=(l.pos+dt*l.v)%1500; l.el.style.strokeDashoffset=String(-l.pos)}
  requestAnimationFrame(animeer);
}
function snelheid(w){return Math.abs(w)<15?0:Math.min(140,18+Math.sqrt(Math.abs(w))*1.6)}
function schema(){
  const svg=$("schema"); if(!svg)return;
  const net=laatsteNet;
  const knopen=K.kaarten.filter(k=>k.stroom_w!=null).slice(0,5);
  const links=[{soort:"net",naam:"Net",w:net}], rechts=[];
  for(const k of knopen){
    const o={soort:k.soort,naam:k.titel,w:k.stroom_w};
    (k.soort==="zon"||k.soort==="batterij"?links:rechts).push(o);
  }
  links.sort((a,b)=>({zon:0,net:1,batterij:2}[a.soort]??3)-({zon:0,net:1,batterij:2}[b.soort]??3));
  const H=Math.max(links.length,rechts.length)>=3?270:200, pos=(n,i)=>n===1?H/2-8:30+i*(H-80)/(n-1);
  svg.setAttribute("viewBox","0 0 320 "+H);
  const huis={x:rechts.length?165:270,y:H/2-8};
  let s="", snel=[];
  const teken=(o,x,y,kant)=>{
    const r=23, w=o.w;
    const x1=kant<0?x+r:x-r, x2=kant<0?huis.x-29:huis.x+29;
    const d="M"+x1+" "+y+" C"+((x1+x2)/2)+" "+y+","+((x1+x2)/2)+" "+huis.y+","+x2+" "+huis.y;
    const kleur=o.soort==="net"?((w||0)<0?"var(--terug)":"var(--afname)"):(KLEUR[o.soort]||"#9fb3c8");
    s+='<path class="lijnbasis" d="'+d+'"/>';
    s+='<path class="lijn" d="'+d+'" stroke="'+kleur+'" style="opacity:'+(w==null||Math.abs(w)<15?0:1)+'"/>';
    // het pad loopt van de knoop naar het huis; w>0 = naar het huis
    snel.push(snelheid(w||0)*((w||0)>0?1:-1));
    s+='<circle class="knoop" cx="'+x+'" cy="'+y+'" r="'+r+'"/>';
    s+='<g transform="translate('+x+' '+y+') scale(.9)" stroke="'+(o.soort==="net"?"var(--zacht)":kleur)+'" stroke-width="2.1" fill="none" stroke-linecap="round" stroke-linejoin="round">'+(IC[o.soort]||IC.ander)+"</g>";
    s+='<text class="knoopw" x="'+x+'" y="'+(y+r+13)+'">'+(w==null?"-":kwKort(Math.abs(w)))+"</text>";
    s+='<text class="knoopnaam" x="'+x+'" y="'+(y+r+25)+'">'+esc(o.naam.length>13?o.naam.slice(0,12)+"…":o.naam)+"</text>";
  };
  links.forEach((o,i)=>teken(o,36,pos(links.length,i),-1));
  rechts.forEach((o,i)=>teken(o,284,pos(rechts.length,i),1));
  let huisW=null;   // verbruik huis = net + wat zon/batterij leveren - wat de auto enz. nemen
  if(knopen.length&&net!=null)huisW=net+knopen.reduce((a,k)=>a+k.stroom_w,0);
  s+='<circle class="knoop" cx="'+huis.x+'" cy="'+huis.y+'" r="29" style="stroke:var(--zacht)"/>';
  s+='<g transform="translate('+huis.x+' '+huis.y+') scale(1.15)" stroke="var(--tekst)" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round">'+IC.huis+"</g>";
  if(huisW!=null)s+='<text class="knoopw" x="'+huis.x+'" y="'+(huis.y+44)+'">'+kwKort(Math.max(0,huisW))+"</text>";
  s+='<text class="knoopnaam" x="'+huis.x+'" y="'+(huis.y+(huisW==null?44:56))+'">Huis</text>';
  svg.innerHTML=s;
  const oud=LIJNEN;
  LIJNEN=[...svg.querySelectorAll(".lijn")].map((el,i)=>({el,v:snel[i],pos:(oud[i]&&oud[i].pos)||0}));
}

function live(){
  fetch("/api/now",{cache:"no-store"}).then(r=>r.json()).then(j=>{
    const l=$("live");
    if(j.status==="geen_data"){l.className="live stil";l.lastChild.textContent="GEEN DATA";$("vermogen").textContent="-";
      $("richting").textContent="nog geen metingen van de meter";laatsteNet=null;schema();return}
    l.className="live"+(j.status==="ok"?"":" stil");
    l.lastChild.textContent=j.status==="ok"?"LIVE":"STIL ("+j.leeftijd_s+" s)";
    const v=j.vermogen_w||0, exp=v<0;
    laatsteNet=v;
    $("vermogen").innerHTML='<span class="'+(exp?"terug":"afname")+'">'+kw(Math.abs(v))+"</span>";
    $("richting").textContent=Math.abs(v)<15?"net in evenwicht":exp?"teruglevering naar het net":"afname van het net";
    schema(); fasen(j);
    const gem=j.kwartier_gemiddeld_w, budget=j.budget_w;
    $("kw-gem").textContent=kw(gem);
    $("kw-budget").textContent=kw(budget);
    $("kw-rest").textContent=j.resterend_w==null?"-":kw(j.resterend_w);
    const pct=gem==null||!budget?0:Math.max(0,Math.min(100,gem/budget*100));
    const boog=$("boog");
    boog.setAttribute("stroke-dasharray",pct.toFixed(1)+" 100");
    boog.setAttribute("stroke-opacity",pct>0?"1":"0");
    boog.setAttribute("stroke",pct>=100?"var(--slecht)":pct>=80?"var(--let)":"var(--goed)");
    laatsteLive={einde:Math.floor(Date.now()/900000+1)*900000,eindeTekst:j.kwartier_einde_lokaal};
    klok();
  }).catch(()=>{const l=$("live");l.className="live stil";l.lastChild.textContent="GEEN VERBINDING"});
}

function fasen(j){
  const n=D?D.fasen:3, max=D?D.hoofdzekering_a:25, h=[];
  for(let i=0;i<n;i++){
    const w=j.fasen_w?j.fasen_w[i]:null, a=j.fasen_a?j.fasen_a[i]:null;
    if(w==null&&a==null)continue;
    const exp=(w||0)<0, amp=a!=null?Math.abs(a):Math.abs(w||0)/230;
    const pct=Math.min(100,amp/max*100);
    const kleur=pct>=90?"var(--slecht)":exp?"var(--terug)":"var(--afname)";
    h.push('<div class="fase"><span class="klein">L'+(i+1)+'</span><div class="balk"><div style="left:0;width:'+pct.toFixed(1)+
      '%;background:'+kleur+'"></div></div><span class="w">'+(w==null?komma(amp,1)+" A":kw(w))+"</span></div>");
  }
  $("fasen").innerHTML=h.length?h.join("")+'<div class="klein" style="text-align:right">balk = stroom t.o.v. hoofdzekering '+komma(max,0)+' A</div>':"";
}

/* ---------- toestelkaarten ---------- */
function toestellen(){
  const h=[];
  for(const k of K.kaarten){
    const kleur=KLEUR[k.soort]||"#9fb3c8";
    let s='<section class="kaart toestel'+(k.verouderd?" oud":"")+'"><div class="bovenaan"><div class="icoon">'+icoon(k.soort,kleur)+'</div><div>'+
      '<div class="naam">'+esc(k.titel)+'</div><div class="stat st-'+esc(k.verouderd?"let":k.status)+'"><i></i>'+
      esc(k.verouderd?"verouderd ("+Math.round(k.leeftijd_s/60)+" min oud)":k.statustekst)+"</div></div></div>";
    if(k.foto)s+='<div class="foto'+(/\.png(\?|$)/i.test(k.foto)?" vrij":"")+'"><img src="'+esc(k.foto)+'" alt="'+esc(k.titel)+'" loading="lazy"></div>';
    if(k.waarde)s+='<div class="getal">'+esc(k.waarde)+"<small>"+esc(k.eenheid)+"</small></div>"+(k.label?'<div class="klein">'+esc(k.label)+"</div>":"");
    if(k.balk!=null)s+='<div class="laad"><div style="width:'+k.balk+'%;background:'+(k.balk<10?"var(--slecht)":kleur)+'"></div></div>';
    if(k.regels&&k.regels.length)s+='<div class="regels">'+k.regels.map(r=>"<span>"+esc(r[0])+"</span><span>"+esc(r[1])+"</span>").join("")+"</div>";
    h.push(s+"</section>");
  }
  for(const p of K.plaatshouders){
    h.push('<section class="kaart toestel leegkaart"><div class="bovenaan"><div class="icoon">'+icoon(p.soort,"var(--zacht)")+'</div><div>'+
      '<div class="naam">'+esc(p.titel)+'</div><div class="stat"><i></i>nog niet gekoppeld</div></div></div>'+
      (p.foto?'<div class="foto'+(/\.png(\?|$)/i.test(p.foto)?" vrij":"")+'"><img src="'+esc(p.foto)+'" alt="" loading="lazy"></div>':"")+
      '<div class="klein">Staat in je vragenlijst. Een module (bv. gebouwd met de AI) kan hier de toestand tonen: zie LEESMIJ, "Kaarten op de tablet".</div></section>');
  }
  $("toestellen").innerHTML=h.join("");
}
function kaarten(){
  fetch("/api/tablet/kaarten",{cache:"no-store"}).then(r=>r.json()).then(k=>{K=k;toestellen();schema()}).catch(()=>{});
}

/* ---------- prijzen ---------- */
function prijzen(){
  const p=D.prijzen, box=$("prijsinhoud");
  $("prijsbron").textContent=D.prijsbron?"kale prijs, bron "+D.prijsbron:"";
  if(!p.length){
    box.innerHTML='<div class="leeg">Nog geen marktprijzen. Kies een prijsbron bij <a href="/instellingen#prijzen">Instellingen</a> (EnergyZero werkt zonder token).</div>';
    return;
  }
  const nu=Date.now();
  const stap=p.length>1?p[1][0]-p[0][0]:3600000;
  const huidig=p.find(x=>x[0]<=nu&&nu<x[0]+stap);
  const toekomst=p.filter(x=>x[0]+stap>nu);
  const vals=p.map(x=>x[1]).slice().sort((a,b)=>a-b);
  const q=f=>vals[Math.min(vals.length-1,Math.floor(f*(vals.length-1)))];
  const laag=q(.25), hoog=q(.75);
  const venster=Math.max(1,Math.round(3600000/stap)); let best=null;
  const kandidaten=toekomst.filter(x=>x[0]<nu+12*3600000);
  for(let i=0;i+venster<=kandidaten.length;i++){
    const s=kandidaten.slice(i,i+venster).reduce((a,b)=>a+b[1],0)/venster;
    if(!best||s<best.p)best={t:kandidaten[i][0],p:s};
  }
  let kop='<div class="prijskop"><div><div class="groot">'+(huidig?komma(huidig[1],2):"-")+
    '<span class="klein" style="font-size:.4em"> c&euro;/kWh nu</span></div></div>';
  if(best)kop+='<div class="klein">Goedkoopste uur (komende 12 u): <b style="color:var(--goed)">'+uur(best.t)+'</b> &middot; '+komma(best.p,2)+' c&euro;</div>';
  const morgen=p.some(x=>x[0]>=D.dag_einde_ms);
  kop+='<div class="klein" style="margin-left:auto">'+(morgen?"vandaag + morgen":"morgen volgt rond 13 u")+"</div></div>";
  const van=D.dag_start_ms, tot=morgen?D.morgen_einde_ms:D.dag_einde_ms;
  const W=1000,H=150,onder=146,boven=6;
  const mn=Math.min(0,vals[0]), mx=Math.max(vals[vals.length-1],1);
  const y=v=>onder-(v-mn)/(mx-mn)*(onder-boven);
  const x=t=>(t-van)/(tot-van)*W, bw=Math.max(1,stap/(tot-van)*W-(stap<=900000?.6:1.5));
  let s="";
  if(mn<0)s+='<line x1="0" x2="'+W+'" y1="'+y(0)+'" y2="'+y(0)+'" stroke="var(--rand)" stroke-width="1"/>';
  for(const [t,v] of p){
    if(t<van||t>=tot)continue;
    const voorbij=t+stap<=nu, isNu=huidig&&t===huidig[0];
    const kleur=isNu?"#ffffff":v<=laag?"var(--goed)":v>=hoog?"var(--slecht)":"#6f8396";
    const y0=y(Math.max(v,0)), y1=y(Math.min(v,0));
    s+='<rect x="'+x(t).toFixed(1)+'" y="'+y0.toFixed(1)+'" width="'+bw.toFixed(2)+'" height="'+Math.max(1,y1-y0).toFixed(1)+
       '" rx="1" fill="'+kleur+'" opacity="'+(voorbij?.35:1)+'"/>';
  }
  if(morgen)s+='<line x1="'+x(D.dag_einde_ms)+'" x2="'+x(D.dag_einde_ms)+'" y1="0" y2="'+onder+'" stroke="var(--zacht)" stroke-dasharray="3 4" stroke-width="1"/>';
  const ticks=[];
  for(let t=van;t<=tot;t+=(morgen?6:3)*3600000)ticks.push([t,uur(t)]);
  box.innerHTML=kop+'<svg class="grafiek" viewBox="0 0 '+W+" "+H+'" preserveAspectRatio="none">'+s+"</svg>"+as(ticks,van,tot)+
    '<div class="legende"><span><i style="background:#fff"></i>nu</span><span><i style="background:var(--goed)"></i>goedkoop (laagste kwart)</span>'+
    '<span><i style="background:#6f8396"></i>gemiddeld</span><span><i style="background:var(--slecht)"></i>duur (hoogste kwart)</span>'+
    '<span>min '+komma(vals[0],1)+' &middot; max '+komma(vals[vals.length-1],1)+' c&euro;/kWh</span></div>';
}
function as(ticks,van,tot){   // tijdas als gewone tekst onder een uitgerekte svg
  return '<div class="as">'+ticks.map(([t,l],i)=>{const p=(t-van)/(tot-van)*100;
    return '<span class="'+(i===0?"l":i===ticks.length-1?"r":"")+'" style="left:'+p.toFixed(2)+'%">'+l+"</span>"}).join("")+"</div>";
}

/* ---------- pieken van de maand, vergelijking met gisteren ---------- */
function pieken(){
  const p=D.pieken||[], min=D.minimum_piek_w||2500;
  if(!p.length){$("pieken").innerHTML='<div class="klein">Nog geen volledige kwartieren deze maand.</div>';return}
  const top=Math.max(p[0].w,min)*1.08;
  $("pieken").innerHTML='<div class="klein" style="margin-bottom:2px">Hoogste kwartieren deze maand (streepje = '+kwKort(min)+')</div>'+
    p.map((x,i)=>'<div class="piek"><span>'+x.tekst+'</span><div class="balk"><div style="width:'+(x.w/top*100).toFixed(1)+'%;'+(i===0?"background:var(--let)":"")+'"></div><i style="left:'+(min/top*100).toFixed(1)+'%"></i></div><span>'+kwKort(x.w)+"</span></div>").join("");
}
function vergelijk(el,nu,toen,meerIsSlecht,dec){
  if(toen==null||nu==null){$(el).textContent="";return}
  const v=nu-toen, pct=toen>=0.1?Math.round(v/toen*100):null;
  const slecht=meerIsSlecht?v>0:v<0;
  $(el).innerHTML="gisteren "+komma(toen,dec==null?1:dec)+(pct==null||Math.abs(v)<0.05||pct===0?"":' <span class="'+(slecht?"op":"neer")+'">'+(v>0?"▲":"▼")+" "+Math.abs(pct)+" %</span>");
}

/* ---------- weer ---------- */
function weerIcoon(code,dag){
  const z='<circle cx="12" cy="12" r="4.5" fill="var(--zon)" stroke="none"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" stroke="var(--zon)"/>';
  const m='<path d="M16 4a7 7 0 1 0 5 11a6 6 0 0 1-5-11z" fill="#c9d4e0" stroke="none"/>';
  const w='<path d="M7 18h10a4 4 0 0 0 0-8a5.5 5.5 0 0 0-10.5 1.5A3.3 3.3 0 0 0 7 18z" fill="#aab8c6" stroke="none"/>';
  const wk='<g transform="translate(1 -2)">'+w+"</g>";
  const r='<path d="M9 19.5l-1 2.5M13 19.5l-1 2.5M17 19.5l-1 2.5" stroke="var(--terug)"/>';
  const s='<path d="M9 21h.01M13 22h.01M17 21h.01" stroke="#fff" stroke-width="2.5"/>';
  const o='<path d="M13 17l-2 3h3l-2 3" stroke="var(--zon)"/>';
  let g;
  if(code===0)g=dag?z:m;
  else if(code<=2)g='<g transform="translate(-3 -3) scale(.8)">'+(dag?z:m)+"</g>"+'<g transform="translate(4 4) scale(.75)">'+w+"</g>";
  else if(code===3)g=w;
  else if(code<=48)g=w+'<path d="M4 21h16M6 23h12" stroke="#aab8c6"/>';
  else if(code<=67||(code>=80&&code<=82))g=wk+r;
  else if(code<=77||code===85||code===86)g=wk+s;
  else g=wk+o;
  return '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round">'+g+"</svg>";
}
const WEERTEKST=c=>c===0?"helder":c<=2?"half bewolkt":c===3?"bewolkt":c<=48?"mist":c<=57?"motregen":c<=67?"regen":c<=77?"sneeuw":c<=82?"buien":c<=86?"sneeuwbuien":"onweer";
function weer(){
  const w=D.weer||{}, kaart=$("k-weer");
  if(w.uit){kaart.style.display="none";$("weerkop").innerHTML="";return}
  kaart.style.display="";
  if(w.fout){$("weerinhoud").innerHTML='<div class="leeg">'+esc(w.fout)+"</div>";$("weerkop").innerHTML="";return}
  $("weerplaats").textContent=w.plaats||"";
  const dagnaam=(d,i)=>i===0?"vandaag":i===1?"morgen":new Date(d.datum+"T12:00").toLocaleDateString("nl-BE",{weekday:"long"});
  $("weerkop").innerHTML=(w.dagen||[]).map((d,i)=>'<div class="d">'+
    (i===0&&w.nu&&w.nu.temp!=null?"<b>"+komma(w.nu.temp,0)+"°</b>":"<b>"+dagnaam(d,i)+"</b>")+
    weerIcoon(i===0&&w.nu?w.nu.code:d.code,i===0&&w.nu?w.nu.dag:1)+komma(d.max,0)+"° / "+komma(d.min,0)+"°</div>").join("");
  let h='<div class="dagen">'+(w.dagen||[]).map((d,i)=>'<div class="wdag"><div class="klein">'+dagnaam(d,i)+"</div>"+
    weerIcoon(d.code,1)+"<b>"+komma(d.max,0)+"°</b> / "+komma(d.min,0)+"°"+
    '<div class="klein">'+WEERTEKST(d.code)+"</div>"+
    '<div class="klein">'+komma(d.neerslag,1)+" mm &middot; "+komma(d.zon_u,1)+" u zon</div>"+
    (d.zon_kwh!=null?'<div style="color:var(--zon);font-weight:650;margin-top:3px">≈ '+komma(d.zon_kwh,1)+" kWh</div>":"")+"</div>").join("")+"</div>";
  const r=(w.dagen||[]).slice(0,2).flatMap(d=>d.straling||[]);
  if(r.length){
    const mx=Math.max(100,...r), W=1000,H=90, b=W/r.length, nuU=(Date.now()-D.dag_start_ms)/3600000;
    let s="";
    r.forEach((v,i)=>{const hh=v/mx*(H-4);s+='<rect x="'+(i*b).toFixed(1)+'" y="'+(H-hh).toFixed(1)+'" width="'+(b*.8).toFixed(1)+'" height="'+hh.toFixed(1)+'" fill="var(--zon)" opacity="'+(i+1<=nuU?.35:.9)+'"/>'});
    s+='<line x1="'+(W/2)+'" x2="'+(W/2)+'" y1="0" y2="'+H+'" stroke="var(--zacht)" stroke-dasharray="3 4"/>';
    h+='<div class="klein">Zoninstraling per uur, vandaag en morgen'+(w.kwp?" (kWh = ruwe schatting voor "+komma(w.kwp,1)+" kWp)":"")+"</div>"+
      '<svg class="grafiek" style="height:64px" viewBox="0 0 '+W+" "+H+'" preserveAspectRatio="none">'+s+"</svg>"+
      as([[0,"00u"],[12,"12u"],[24,"morgen"],[36,"12u"],[48,"24u"]],0,48);
  }
  $("weerinhoud").innerHTML=h;
}

function dagGrafiek(){
  const pts=D.vermogen, svg=$("dag"), W=1000, H=150, van=D.dag_start_ms, tot=D.dag_einde_ms;
  $("dag-leeg").style.display=pts.length?"none":"block";
  if(!pts.length){svg.innerHTML="";return}
  let mx=Math.max(500,...pts.map(p=>p[1])), mn=Math.min(0,...pts.map(p=>p[1]));
  mx*=1.08; mn*=1.08;
  const y=v=>(mx-v)/(mx-mn)*(H-6)+3, x=t=>(t-van)/(tot-van)*W, b=x(van+300000)-x(van);
  let s='<line x1="0" x2="'+W+'" y1="'+y(0)+'" y2="'+y(0)+'" stroke="var(--rand)" stroke-width="1"/>';
  for(let h=3;h<24;h+=3){const xx=h/24*W;s+='<line x1="'+xx+'" x2="'+xx+'" y1="0" y2="'+H+'" stroke="var(--rand)" stroke-width="1" stroke-dasharray="2 5" opacity=".6"/>'}
  for(const [t,v] of pts){
    const y0=y(Math.max(v,0)), y1=y(Math.min(v,0));
    s+='<rect x="'+x(t).toFixed(1)+'" y="'+y0.toFixed(1)+'" width="'+(b*.8).toFixed(2)+'" height="'+Math.max(.8,y1-y0).toFixed(1)+'" fill="'+(v<0?"var(--terug)":"var(--afname)")+'"/>';
  }
  const nx=x(Date.now());
  if(nx>0&&nx<W)s+='<line x1="'+nx+'" x2="'+nx+'" y1="0" y2="'+H+'" stroke="#fff" stroke-width="1.5" opacity=".7"/>';
  svg.innerHTML=s;
  const ticks=[]; for(let h=0;h<=24;h+=3)ticks.push([van+h*3600000,String(h).padStart(2,"0")+"u"]);
  $("dag-as").outerHTML=as(ticks,van,van+24*3600000).replace('class="as"','class="as" id="dag-as"');
}

function zwaar(){
  fetch("/api/tablet",{cache:"no-store"}).then(r=>r.json()).then(d=>{
    D=d; K={kaarten:d.kaarten,plaatshouders:d.plaatshouders};
    $("demo").style.display=d.demo?"block":"none";
    const v=d.vandaag, g=d.gisteren||{};
    $("t-afname").textContent=komma(v.afname_kwh,2);
    $("t-afname-sub").textContent="kWh (dag "+komma(v.afname_dag_kwh,1)+" / nacht "+komma(v.afname_nacht_kwh,1)+")";
    $("t-terug").textContent=komma(v.teruglevering_kwh,2);
    $("t-kost").textContent=euro(v.kosten_eur);
    $("t-kost-sub").innerHTML=d.tarief_ingevuld?"alles inbegrepen":'<a href="/instellingen#tarief" style="color:var(--let)">contract invullen</a>';
    $("t-maand").textContent=euro(v.maand_eur);
    vergelijk("g-afname",v.afname_kwh,g.afname_tot_nu_kwh,true);
    vergelijk("g-terug",v.teruglevering_kwh,g.terug_tot_nu_kwh,false);
    $("g-kost").textContent=g.kosten_eur==null?"":"gisteren hele dag "+euro(g.kosten_eur);
    const gw=d.gas_water||{};
    $("tg-gas").style.display=gw.gas?"":"none"; $("tg-water").style.display=gw.water?"":"none";
    document.querySelector(".tegels").classList.toggle("zes",!!(gw.gas&&gw.water));
    const kostRegel=x=>x.kosten_vandaag==null?'<a href="/instellingen#gaswater" style="color:var(--let)">prijs invullen</a>':
      euro(x.kosten_vandaag)+" vandaag &middot; maand "+euro(x.kosten_maand);
    if(gw.gas){$("t-gas").textContent=komma(gw.gas.vandaag,2);$("t-gas-sub").innerHTML="m³ &middot; "+kostRegel(gw.gas);vergelijk("g-gas",gw.gas.vandaag,gw.gas.gisteren_tot_nu,true,2)}
    if(gw.water){$("t-water").textContent=komma(gw.water.vandaag,0);$("t-water-sub").innerHTML="liter &middot; "+kostRegel(gw.water);vergelijk("g-water",gw.water.vandaag,gw.water.gisteren_tot_nu,true,0)}
    $("verg-uitleg").textContent=g.zelfde_uur?"gisteren = tot "+g.zelfde_uur:"";
    $("kw-vandaag").textContent=kw(v.hoogste_kwartier_w);
    pieken(); prijzen(); dagGrafiek(); weer(); toestellen(); schema();
    $("voet").innerHTML="Bijgewerkt "+uur(d.nu_ms,{second:"2-digit"})+" &middot; <b>meten</b>, geen sturing"+
      (d.prijsbron?" &middot; prijzen "+d.prijsbron:"")+(d.weer&&d.weer.dagen?" &middot; weer Open-Meteo":"")+" &middot; HEMS-meetsysteem";
  }).catch(()=>{});
}

$("volscherm").onclick=()=>{
  const e=document.documentElement;
  if(document.fullscreenElement)document.exitFullscreen();
  else if(e.requestFullscreen)e.requestFullscreen().catch(()=>{});
  else if(e.webkitRequestFullscreen)e.webkitRequestFullscreen();
};
async function wakker(){try{if("wakeLock" in navigator)await navigator.wakeLock.request("screen")}catch(e){}}
document.addEventListener("visibilitychange",()=>{if(!document.hidden){wakker();live();zwaar()}});
let muisTimer;document.addEventListener("mousemove",()=>{document.body.classList.remove("verberg");clearTimeout(muisTimer);muisTimer=setTimeout(()=>document.body.classList.add("verberg"),5000)});

wakker(); klok(); zwaar(); live(); requestAnimationFrame(animeer);
setInterval(klok,1000); setInterval(live,5000); setInterval(kaarten,10000); setInterval(zwaar,60000);
setTimeout(()=>location.reload(),6*3600000);
</script>
</body></html>
"""


def pagina(tz):
    return PAGINA.replace("__TZ__", json.dumps(str(tz)))
