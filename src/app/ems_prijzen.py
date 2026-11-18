#!/usr/bin/env python3
"""ems_prijzen - haalt de day-ahead-marktprijzen op bij de geconfigureerde provider-API.

Ondersteunde bronnen (automatisch herkend aan het antwoordformaat):
  - JSON in de stijl van EnergyZero (https://api.energyzero.nl/v1/energyprices,
    geen token nodig);
  - ENTSO-E transparantie-XML (https://web-api.tp.entsoe.eu/api, gratis token
    nodig; het token staat in .env als HEMS_PROVIDER_TOKEN, nooit in de config).

Werkt er geen provider geconfigureerd of antwoordt ze niet, dan stopt dit
programma zonder fout: het meetsysteem draait gewoon verder zonder prijzen.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import ems_common as ec


def _haal(url, timeout=30):
    verzoek = urllib.request.Request(url, headers={"User-Agent": "hems-meting/1.0"})
    with urllib.request.urlopen(verzoek, timeout=timeout) as r:
        return r.read()


def _parse_dt(s):
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"onbegrepen tijdstip: {s}")


def parse_json_prijzen(data):
    """EnergyZero-stijl: {"Prices": [{"price": ..., "readingDate": ...}, ...]}."""
    obj = json.loads(data.decode("utf-8"))
    lijst = obj.get("Prices") if isinstance(obj, dict) else obj
    if not isinstance(lijst, list):
        raise ValueError("geen prijzenlijst gevonden in het JSON-antwoord")
    uit = []
    for p in lijst:
        if not isinstance(p, dict):
            continue
        sleutels = {k.lower(): k for k in p}
        k_prijs = sleutels.get("price")
        k_tijd = sleutels.get("readingdate") or sleutels.get("timestamp") or sleutels.get("datetime")
        if k_prijs is None or k_tijd is None:
            continue
        uit.append((_parse_dt(str(p[k_tijd])), float(p[k_prijs])))
    if not uit:
        raise ValueError("geen bruikbare prijspunten in het JSON-antwoord")
    return uit


def parse_entsoe_xml(data):
    """ENTSO-E day-ahead (documentType A44): prijzen in EUR/MWh -> EUR/kWh."""
    root = ET.fromstring(data)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    if "Acknowledgement" in root.tag:            # foutbericht van ENTSO-E (bv. geen data of fout token)
        tekst = " ".join(t.text.strip() for t in root.iter(ns + "text") if t.text)
        raise ValueError(f"ENTSO-E meldt: {tekst or 'geen gegevens'}")
    uit = []
    for periode in root.iter(ns + "Period"):
        interval = periode.find(ns + "timeInterval")
        start = _parse_dt(interval.find(ns + "start").text)
        res = periode.find(ns + "resolution").text.strip()
        stap = timedelta(minutes=15) if res == "PT15M" else timedelta(hours=1)
        for punt in periode.findall(ns + "Point"):
            pos = int(punt.find(ns + "position").text)
            prijs = float(punt.find(ns + "price.amount").text) / 1000.0
            uit.append((start + (pos - 1) * stap, prijs))
    if not uit:
        raise ValueError("geen prijspunten gevonden in het ENTSO-E-antwoord")
    return uit


def bouw_url(cfg, token, dag_start_utc, dag_einde_utc):
    """Maak de ophaal-URL voor de provider; voegt parameters toe als die ontbreken."""
    basis = ec.cfg_str(cfg, "provider", "api_url")
    gebied = ec.cfg_str(cfg, "provider", "gebied", "10YBE----------2")
    if "entsoe" in basis.lower():
        q = {
            "securityToken": token,
            "documentType": "A44",
            "in_Domain": gebied,
            "out_Domain": gebied,
            "periodStart": dag_start_utc.strftime("%Y%m%d0000"),
            "periodEnd": dag_einde_utc.strftime("%Y%m%d0000"),
        }
        return basis + ("&" if "?" in basis else "?") + urllib.parse.urlencode(q)
    if "?" not in basis:          # EnergyZero-stijl: voeg standaardparameters toe
        q = {
            "fromDate": dag_start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "tillDate": dag_einde_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "interval": "4", "usageType": "1", "inclBtw": "true",
        }
        return basis + "?" + urllib.parse.urlencode(q)
    return basis


def haal(cfg, cfgpad, dagen=2):
    """Haal de day-ahead-prijzen op. Geeft (lijst van (start_utc, eur_kwh), bron); fout = uitzondering."""
    url_basis = ec.cfg_str(cfg, "provider", "api_url")
    if not url_basis:
        raise ValueError("geen bron voor marktprijzen ingesteld")
    token = ec.lees_env(ec.pad_in_project(cfgpad, ".env")).get("HEMS_PROVIDER_TOKEN", "")
    if "entsoe" in url_basis.lower() and not token:
        raise ValueError("ENTSO-E heeft een token nodig")
    vandaag = ec.utc_nu().replace(hour=0, minute=0, second=0, microsecond=0)
    url = bouw_url(cfg, token, vandaag, vandaag + timedelta(days=max(dagen, 1)))
    try:
        data = _haal(url)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("de bron weigert de toegang: klopt de token?") from e
        try:
            data = e.read()
            parse_entsoe_xml(data)                 # haalt de foutmelding van ENTSO-E eruit
        except ValueError:
            raise
        except Exception:
            pass
        raise ValueError(f"de bron antwoordt met fout {e.code}") from e
    if data.lstrip()[:1] in (b"{", b"["):
        return parse_json_prijzen(data), "json"
    return parse_entsoe_xml(data), "entsoe"


def bewaar(cfgpad, cfg, prijzen, bron):
    db = ec.open_db(ec.db_pad(cfgpad, cfg))
    with db:
        for start_utc, prijs in prijzen:
            db.execute("INSERT OR REPLACE INTO prijzen(start_utc, eur_kwh, bron) VALUES(?,?,?)",
                       (ec.iso(start_utc), round(prijs, 5), bron))
    db.close()


def main():
    ap = argparse.ArgumentParser(description="HEMS-prijzen ophalen (day-ahead)")
    ap.add_argument("--config", required=True, help="pad naar ems.ini")
    ap.add_argument("--dagen", type=int, default=2, help="aantal dagen ophalen vanaf vandaag (standaard 2)")
    args = ap.parse_args()
    cfg = ec.laad_config(args.config)

    if not ec.cfg_str(cfg, "provider", "api_url"):
        print("geen provider.api_url geconfigureerd: niets te doen")
        return 0
    try:
        prijzen, bron = haal(cfg, args.config, args.dagen)
    except (urllib.error.URLError, OSError) as e:
        print(f"provider niet bereikbaar ({e}); het meetsysteem draait gewoon verder", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"prijzen ophalen mislukt: {e}; controleer de instellingen (Instellingen -> Marktprijzen)", file=sys.stderr)
        return 1
    bewaar(args.config, cfg, prijzen, bron)
    print(f"{len(prijzen)} prijspunten opgeslagen ({prijzen[0][0].date()} t/m {prijzen[-1][0].date()}, bron: {bron})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
