"""ems_vragenlijst - vragenlijst "Mijn installatie" voor wie later met een AI-assistent sturing wil bouwen.

De vragen volgen blok A tot I van het HEMS-template (fase 1), in kioskstijl op een pagina onder Instellingen.
Wat het systeem al weet (meter, fasen, gemeente, contract, maandpiek) vult het zelf in. De antwoorden komen in
config/vragenlijst.json; bij elke opslag schrijft het systeem ook mijn-installatie.md in de projectmap. Dat
bestand geef je samen met het template aan je AI-assistent: die stelt dan enkel nog vragen over wat ontbreekt.

Toestellen zoeken gebeurt enkel lezend (GET op poort 80) en enkel bij toestellen die dat veilig toelaten.
Thuisbatterijen worden bewust NIET aangesproken: veel batterijen (bv. Marstek Venus E) aanvaarden maar één
Modbus-verbinding, en een zoekpoging kan een bestaande sturing storen.
"""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import ems_common as ec

BESTAND = "vragenlijst.json"          # in de configmap
UITVOER = "mijn-installatie.md"        # in de projectmap


def _ja_nee():
    return [["", "— kies —"], ["ja", "Ja"], ["nee", "Nee"], ["weet niet", "Weet ik niet"]]


DOELEN = [
    ("doel_zon", "Zo veel mogelijk eigen zonnestroom gebruiken"),
    ("doel_factuur", "Zo laag mogelijke energiefactuur"),
    ("doel_piek", "Zo laag mogelijke kwartierpiek (capaciteitstarief)"),
    ("doel_injectie", "Zo weinig mogelijk teruglevering"),
    ("doel_co2", "Zo weinig mogelijk CO2 (stroom gebruiken als het net het groenst is)"),
    ("doel_sparen", "Batterijen sparen (minder cycli, niet altijd 100 %)"),
    ("doel_reserve", "Reserve houden voor stroomuitval"),
    ("doel_auto", "De auto altijd op tijd genoeg geladen"),
    ("doel_rust", "Zo weinig mogelijk ingrijpen: het moet gewoon werken"),
]

TOESTELLEN_G = [
    ("g_warmtepomp", "Warmtepomp"), ("g_boiler", "Elektrische boiler"), ("g_airco", "Airco"),
    ("g_pomp", "Zwembad- of vijverpomp"), ("g_witgoed", "Was-, droog- of vaatwasmachine"),
    ("g_stekkers", "Slimme stekkers (Shelly, Tapo, ...)"), ("g_verwarming", "Elektrische verwarming"),
]


def secties():
    """De blokken van de vragenlijst, in dezelfde vorm als de secties van de pagina Instellingen."""
    return [
        {"id": "vl_start", "titel": "Start", "icoon": "👋",
         "intro": "Deze vragenlijst is voor later, als je met een AI-assistent ook <b>sturing</b> wil laten bouwen "
                  "(batterij, laadpaal, warmtepomp, ...) volgens het HEMS-template. Wat je hier invult, komt in het "
                  "bestand <b>mijn-installatie.md</b>; de assistent leest dat en vraagt enkel nog wat ontbreekt. "
                  "Niets is verplicht: sla over wat je niet weet. Het meetsysteem zelf verandert er niet door.",
         "velden": [
             {"id": "profiel", "type": "keuze", "label": "Wat wil je bereiken?",
              "opties": [["", "— kies —"],
                         ["advies", "Advies vóór aankoop: ik heb nog (bijna) niets of twijfel"],
                         ["inzicht", "Inzicht: enkel meten en rapporten (dat doet dit systeem al)"],
                         ["tarief", "Tarief uittesten: mijn contract vergelijken met andere, ook dynamische"],
                         ["verschuiven", "Verbruik verschuiven zonder batterij (auto, boiler, warmtepomp)"],
                         ["volledig", "Volledig systeem: batterijen, zon, auto, toestellen sturen"]]},
             {"id": "k_doelen", "type": "kop", "label": "Je drie belangrijkste doelen",
              "hulp": "Vink er drie aan (meer mag, maar dan is het minder duidelijk wat voorgaat)."},
             *[{"id": i, "type": "vink", "label": t} for i, t in DOELEN],
             {"id": "k_werk", "type": "kop", "label": "Hoe wil je werken"},
             {"id": "observatie", "type": "keuze", "label": "Mag het systeem meteen sturen?",
              "opties": [["", "— kies —"],
                         ["eerst kijken", "Eerst een tijd enkel tonen wat het zou doen (aanbevolen)"],
                         ["meteen", "Meteen sturen, na de tests"]]},
             {"id": "budget", "type": "keuze", "label": "Mag er iets kosten?",
              "opties": [["", "— kies —"], ["gratis", "Enkel gratis diensten"],
                         ["klein", "Kleine aankopen mogen (ESP32, Modbus-adapter: enkele tientjes)"],
                         ["meer", "Ook betaalde diensten of licenties"]]},
             {"id": "plannen", "type": "tekst", "label": "Plannen voor de komende jaren",
              "plaats": "bv. extra panelen in 2027, warmtepomp, tweede auto"},
         ]},
        {"id": "vl_aansluiting", "titel": "Aansluiting en contract", "icoon": "🔌",
         "intro": "Je gemeente, netbeheerder, contract, fasen en hoofdzekering haalt het systeem uit de pagina "
                  "Instellingen; die staan automatisch in het bestand. Hier komen de vragen die daar niet in staan.",
         "velden": [
             {"id": "woning", "type": "keuze", "label": "Eigenaar of huurder?",
              "opties": [["", "— kies —"], ["eigenaar", "Eigenaar"], ["huurder", "Huurder"],
                         ["mede-eigenaar", "Mede-eigenaar (appartement, VME)"]],
              "hulp": "Huurders en mede-eigenaars hebben voor panelen, batterijen of een laadpaal vaak toestemming nodig."},
             {"id": "personen", "type": "tekst", "label": "Hoeveel personen zijn er gedomicilieerd?", "plaats": "bv. 2",
              "hulp": "Helpt het verbruik in te schatten. Voor water telt het ook mee: in Vlaanderen betaal je het "
                      "basistarief voor 30 m³ per woning plus 30 m³ per gedomicilieerde, daarboven het dubbele."},
             {"id": "oude_regeling", "type": "keuze", "label": "Heb je nog een oude regeling?",
              "opties": [["", "— kies —"], ["geen", "Nee"], ["terugdraaiende teller", "Terugdraaiende teller"],
                         ["prosumententarief", "Prosumententarief"], ["weet niet", "Weet ik niet"]]},
             {"id": "dynamisch", "type": "keuze", "label": "Overweeg je een dynamisch contract?",
              "opties": [["", "— kies —"], ["nee", "Nee"], ["misschien", "Misschien: eerst vergelijken"],
                         ["heb ik al", "Heb ik al"]]},
             {"id": "injectielimiet", "type": "tekst", "label": "Injectielimiet of andere regel van de netbeheerder",
              "plaats": "leeg = geen of weet niet", "leeg_mag": True},
             {"id": "energiedelen", "type": "keuze", "label": "Energiedelen of een energiegemeenschap?",
              "opties": _ja_nee()},
         ]},
        {"id": "vl_meter", "titel": "Meter", "icoon": "⚡",
         "intro": "De meter staat al in de pagina Instellingen. Hier enkel wat erbij hoort.",
         "velden": [
             {"id": "gas", "type": "keuze", "label": "Ook gas meten?", "opties": _ja_nee()},
             {"id": "water", "type": "keuze", "label": "Ook water meten?", "opties": _ja_nee()},
             {"id": "tussenmeters", "type": "tekst", "label": "Andere tussenmeters",
              "plaats": "bv. aparte meter voor de warmtepomp", "leeg_mag": True},
         ]},
        {"id": "vl_zon", "titel": "Zonnepanelen", "icoon": "☀️",
         "velden": [
             {"id": "heeft", "type": "keuze", "label": "Zonnepanelen?", "opties": _ja_nee()},
             {"id": "kwp", "type": "tekst", "label": "Totaal vermogen (kWp)", "plaats": "bv. 5,2",
              "toon": ["heeft", "ja"]},
             {"id": "vlakken", "type": "tekst", "label": "Dakvlakken: oriëntatie, helling, kWp per vlak",
              "plaats": "bv. zuid 35° 3 kWp, west 30° 2,2 kWp", "toon": ["heeft", "ja"]},
             {"id": "omvormer", "type": "tekst", "label": "Merk en type omvormer(s)", "plaats": "bv. SMA Sunny Boy 3.0",
              "toon": ["heeft", "ja"]},
             {"id": "uitlezen", "type": "keuze", "label": "Kan de omvormer lokaal uitgelezen worden?",
              "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["modbus", "Ja, via Modbus TCP of SunSpec"], ["api", "Ja, via een lokale web-API"],
                         ["bluetooth", "Enkel Bluetooth (kan met een ESP32)"], ["cloud", "Enkel via de cloud"],
                         ["weet niet", "Weet ik niet"]]},
             {"id": "voorspelling", "type": "keuze", "label": "Zonvoorspelling",
              "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["forecast.solar", "Forecast.Solar (gratis, geen account)"],
                         ["solcast", "Solcast (gratis account, nauwkeuriger)"], ["geen", "Geen"],
                         ["assistent kiest", "Laat de assistent kiezen"]]},
         ]},
        {"id": "vl_batterij", "titel": "Thuisbatterij", "icoon": "🔋",
         "velden": [
             {"id": "heeft", "type": "keuze", "label": "Thuisbatterij(en)?", "opties": _ja_nee()},
             {"id": "soort", "type": "keuze", "label": "Wat voor systeem?", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["ac", "Losse batterij op het stopcontact of met eigen omvormer (AC)"],
                         ["hybride", "Hybride omvormer met batterij (DC)"], ["beide", "Allebei"]]},
             {"id": "merk", "type": "keuze", "label": "Merk", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"]] + [[m, m] for m in ("Marstek Venus E", "Marstek (ander type)", "Zendure",
                         "Anker SOLIX", "Sungrow", "Huawei", "SolarEdge", "GoodWe", "Victron", "Tesla Powerwall",
                         "ander")]},
             {"id": "aantal", "type": "tekst", "label": "Aantal toestellen", "plaats": "bv. 2", "toon": ["heeft", "ja"]},
             {"id": "kwh", "type": "tekst", "label": "Capaciteit per toestel (kWh)", "plaats": "bv. 5,12",
              "toon": ["heeft", "ja"]},
             {"id": "vermogen", "type": "tekst", "label": "Maximaal laad-/ontlaadvermogen per toestel (W)",
              "plaats": "bv. 2500", "toon": ["heeft", "ja"]},
             {"id": "plaats", "type": "keuze", "label": "Waar staan ze?", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["binnen", "Binnen (verwarmd)"],
                         ["koud", "Onverwarmd (garage, berging): in de winter laden ze soms niet"]]},
             {"id": "aangemeld", "type": "keuze", "label": "Aangemeld bij de netbeheerder?", "toon": ["heeft", "ja"],
              "opties": _ja_nee(), "hulp": "In Vlaanderen verplicht."},
             {"id": "huidige_sturing", "type": "keuze", "label": "Wat stuurt ze nu?", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["eigen modus", "Hun eigen automatische modus"], ["app-schema", "Een schema in de app"],
                         ["home assistant", "Home Assistant"], ["niets", "Niets"], ["weet niet", "Weet ik niet"]],
              "hulp": "Er mag maar één sturing tegelijk zijn; de assistent zet de andere uit en noteert hoe je ze terugzet."},
             {"id": "laden", "type": "keuze", "label": "Hoe mogen ze laden?", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["enkel zon", "Enkel uit zonne-overschot, nooit uit het net"],
                         ["ook goedkope uren", "Ook uit het net op goedkope uren (dynamisch contract)"],
                         ["ook negatieve prijzen", "Ook uit het net bij negatieve prijzen"]]},
             {"id": "ontladen", "type": "keuze", "label": "Hoe mogen ze ontladen?", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["eigen verbruik", "Enkel voor eigen verbruik, nooit naar het net"],
                         ["ook naar net", "Ook naar het net bij hoge prijzen"],
                         ["piek", "Vooral om de kwartierpiek te drukken"]]},
             {"id": "min_soc", "type": "tekst", "label": "Minimum laadtoestand (%)", "plaats": "bv. 10, of 20 in de winter",
              "toon": ["heeft", "ja"], "leeg_mag": True},
             {"id": "auto_voeden", "type": "keuze", "label": "Mogen de batterijen de auto voeden?", "toon": ["heeft", "ja"],
              "opties": _ja_nee()},
         ]},
        {"id": "vl_auto", "titel": "Elektrische auto", "icoon": "🚗",
         "velden": [
             {"id": "heeft", "type": "keuze", "label": "Elektrische auto?", "opties": _ja_nee()},
             {"id": "laadpaal", "type": "keuze", "label": "Laadpaal", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"]] + [[m, m] for m in ("go-e Charger", "Wallbox", "Alfen", "Easee", "Zaptec",
                         "ABB", "Smappee", "geen (gewoon stopcontact)", "ander")]},
             {"id": "fasen_schakelen", "type": "keuze", "label": "Kan de laadpaal tussen 1 en 3 fasen schakelen?",
              "toon": ["heeft", "ja"], "opties": _ja_nee()},
             {"id": "auto", "type": "tekst", "label": "Merk en type auto", "plaats": "bv. Tesla Model 3",
              "toon": ["heeft", "ja"]},
             {"id": "uitlezen", "type": "keuze", "label": "Kan de laadtoestand van de auto gelezen worden?",
              "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["teslamate", "Ja, via TeslaMate (Tesla)"],
                         ["home assistant", "Ja, via Home Assistant"], ["nee", "Nee"], ["weet niet", "Weet ik niet"]]},
             {"id": "strategie", "type": "keuze", "label": "Hoe wil je laden?", "toon": ["heeft", "ja"],
              "opties": [["", "— kies —"], ["zon", "Enkel op zonne-overschot"],
                         ["minimum+zon", "Altijd een minimum, de rest op zon"],
                         ["min-range", "Onder een minimum aantal km altijd laden, anders op zon"],
                         ["vertrektijd", "Klaar tegen een vertrektijd, zo goedkoop mogelijk"],
                         ["goedkoopste uren", "Goedkoopste uren (dynamisch contract)"],
                         ["handmatig", "Handmatig: het systeem kijkt enkel toe"]]},
             {"id": "min_km", "type": "tekst", "label": "Minimum aantal km", "plaats": "bv. 100",
              "toon": ["strategie", "min-range"]},
         ]},
        {"id": "vl_toestellen", "titel": "Andere toestellen", "icoon": "🏠",
         "velden": [
             {"id": "k_stuurbaar", "type": "kop", "label": "Welke toestellen zou je willen laten sturen?"},
             *[{"id": i, "type": "vink", "label": t} for i, t in TOESTELLEN_G],
             {"id": "prioriteit", "type": "tekst", "label": "Voorrang bij zonne-overschot",
              "plaats": "bv. auto > boiler > batterij > net", "leeg_mag": True},
             {"id": "niet_sturen", "type": "tekst", "label": "Grote verbruikers die je NIET wil laten sturen",
              "plaats": "bv. elektrisch vuurtje, microgolfoven, inductie", "leeg_mag": True,
              "hulp": "Handig om pieken in de rapporten te verklaren."},
         ]},
        {"id": "vl_systeem", "titel": "Machine en meldingen", "icoon": "🖥️",
         "velden": [
             {"id": "altijd_aan", "type": "keuze", "label": "Staat deze pc altijd aan?", "opties": _ja_nee()},
             {"id": "ups", "type": "keuze", "label": "Hangt hij aan een UPS (noodstroom)?", "opties": _ja_nee()},
             {"id": "home_assistant", "type": "keuze", "label": "Home Assistant",
              "opties": [["", "— kies —"], ["heb ik", "Heb ik al"], ["gewenst", "Wil ik erbij"], ["nee", "Niet nodig"]]},
             {"id": "meldingen", "type": "keuze", "label": "Meldingen bij problemen via",
              "opties": [["", "— kies —"], ["mail", "Mail"], ["telegram", "Telegram"], ["ntfy", "ntfy (app)"],
                         ["home assistant", "Home Assistant-app"], ["geen", "Geen"]]},
             {"id": "buitenaf", "type": "keuze", "label": "Toegang van buitenaf",
              "opties": [["", "— kies —"], ["niet", "Niet nodig"], ["vpn", "Via een VPN (bv. Tailscale)"],
                         ["weet niet", "Weet ik niet"]],
              "hulp": "Nooit poorten openzetten in de router."},
             {"id": "backup", "type": "keuze", "label": "Back-up naar",
              "opties": [["", "— kies —"], ["nas", "Een NAS"], ["schijf", "Een externe schijf"], ["cloud", "De cloud"],
                         ["nog niet", "Heb ik nog niet"]]},
             {"id": "gezin", "type": "keuze", "label": "Gebruikt een niet-technisch gezinslid het systeem ook?",
              "opties": _ja_nee()},
         ]},
        {"id": "vl_weergave", "titel": "Weergave", "icoon": "👓",
         "velden": [
             {"id": "kleurenblind", "type": "keuze", "label": "Kleurenblind (jij of iemand die de grafieken leest)?",
              "opties": [["", "— kies —"], ["nee", "Nee"], ["rood-groen", "Rood-groen"], ["blauw-geel", "Blauw-geel"],
                         ["volledig", "Volledig"], ["weet niet", "Weet ik niet"]]},
             {"id": "toestel", "type": "keuze", "label": "Waar lees je het dashboard vooral?",
              "opties": [["", "— kies —"], ["gsm", "Gsm"], ["tablet", "Tablet"], ["computer", "Computer"]]},
             {"id": "groot", "type": "vink", "label": "Grote letters"},
             {"id": "papier", "type": "vink", "label": "Rapporten liefst op papier"},
         ]},
        {"id": "vl_toestellen_zoeken", "titel": "Toestellen zoeken", "icoon": "🔎",
         "intro": "Het systeem zoekt toestellen op je thuisnetwerk die het veilig kan herkennen: HomeWizard, go-e "
                  "Charger en Shelly. Het leest enkel (geen enkel toestel wordt gewijzigd). Thuisbatterijen worden "
                  "bewust niet aangesproken: veel batterijen aanvaarden maar één verbinding.",
         "velden": []},
        {"id": "vl_klaar", "titel": "Klaar: het bestand", "icoon": "📄", "velden": []},
    ]


# ---------- opslag ----------

def _pad(cfgpad):
    return os.path.join(os.path.dirname(os.path.abspath(cfgpad)), BESTAND)


def uitvoer_pad(cfgpad):
    return ec.pad_in_project(cfgpad, UITVOER)


def lees(cfgpad):
    try:
        with open(_pad(cfgpad), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def waarden(cfgpad):
    d = lees(cfgpad)
    uit = {}
    for s in secties():
        w = {}
        for v in s["velden"]:
            if v["type"] == "kop":
                continue
            w[v["id"]] = d.get(s["id"], {}).get(v["id"], False if v["type"] == "vink" else "")
        uit[s["id"]] = w
    uit["vl_toestellen_zoeken"] = {"gevonden": d.get("vl_toestellen_zoeken", {}).get("gevonden", [])}
    return uit


def overzicht(cfgpad):
    """Bolletje per blok: ok = iets ingevuld, opt = nog leeg (niets is verplicht)."""
    d = lees(cfgpad)
    uit = []
    for s in secties():
        if s["id"] == "vl_klaar":
            continue
        g = d.get(s["id"], {})
        ingevuld = any(v not in ("", False, None, []) for v in g.values())
        uit.append({"id": s["id"], "staat": "ok" if ingevuld else "opt"})
    return uit


def opslaan(sectie, w, cfg, cfgpad, db_pad):
    geldig = {s["id"]: s for s in secties()}
    if sectie not in geldig or sectie in ("vl_klaar", "vl_toestellen_zoeken"):
        return {"ok": False, "fout": "Onbekend onderdeel."}
    w = w if isinstance(w, dict) else {}
    s = geldig[sectie]
    schoon = {}
    for v in s["velden"]:
        if v["type"] == "kop":
            continue
        x = w.get(v["id"])
        if v["type"] == "vink":
            schoon[v["id"]] = bool(x)
        elif v["type"] == "keuze":
            x = str(x or "")
            if x not in [o[0] for o in v["opties"]]:
                return {"ok": False, "fout": f"Ongeldige keuze bij '{v['label']}'."}
            schoon[v["id"]] = x
        else:
            x = str(x or "").replace("\n", " ").strip()
            if len(x) > 300:
                return {"ok": False, "fout": f"'{v['label']}' is te lang (max. 300 tekens)."}
            schoon[v["id"]] = x
    d = lees(cfgpad)
    d[sectie] = schoon
    d["bijgewerkt"] = ec.iso(ec.utc_nu())
    _bewaar(d, cfg, cfgpad, db_pad)
    return {"ok": True, "bericht": "Opgeslagen. Het bestand mijn-installatie.md is bijgewerkt."}


def _bewaar(d, cfg, cfgpad, db_pad):
    ec._schrijf_atomair(_pad(cfgpad), json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    ec._schrijf_atomair(uitvoer_pad(cfgpad), markdown(cfg, cfgpad, db_pad))


# ---------- toestellen zoeken (alleen lezen) ----------

def _goe_info(ip):
    try:
        d = ec.lees_json(f"http://{ip}/api/status?filter=sse,typ,fwv,fna", timeout=2)
    except Exception:
        return None
    if isinstance(d, dict) and ("sse" in d or "fwv" in d):
        return {"ip": ip, "soort": "go-e Charger", "naam": str(d.get("fna") or "go-e Charger"),
                "detail": f"firmware {d.get('fwv', '?')}"}
    return None


def _shelly_info(ip):
    try:
        d = ec.lees_json(f"http://{ip}/shelly", timeout=2)
    except Exception:
        return None
    if isinstance(d, dict) and ("mac" in d or "type" in d or "model" in d):
        return {"ip": ip, "soort": "Shelly", "naam": str(d.get("name") or d.get("model") or d.get("type") or "Shelly"),
                "detail": f"model {d.get('model') or d.get('type') or '?'}"}
    return None


def _herken(ip):
    hw = ec._homewizard_info(ip)
    if hw:
        return {"ip": ip, "soort": "HomeWizard", "naam": hw["naam"],
                "detail": "P1-meter" if hw["p1"] else hw["type"]}
    return _goe_info(ip) or _shelly_info(ip)


def zoek_toestellen(cfg, cfgpad, db_pad):
    netten = ec.eigen_netten()
    kandidaten = [str(h) for n in netten for h in n.hosts()]
    with ThreadPoolExecutor(max_workers=64) as ex:
        open_ = [ip for ip, ok in zip(kandidaten, ex.map(ec._poort_open, kandidaten)) if ok]
    with ThreadPoolExecutor(max_workers=16) as ex:
        gevonden = [i for i in ex.map(_herken, open_) if i]
    gevonden.sort(key=lambda i: (i["soort"], tuple(int(x) for x in i["ip"].split("."))))
    d = lees(cfgpad)
    d["vl_toestellen_zoeken"] = {"gevonden": gevonden, "gezocht": ec.iso(ec.utc_nu())}
    _bewaar(d, cfg, cfgpad, db_pad)
    return {"ok": True, "netten": [str(n) for n in netten], "toestellen": gevonden}


# ---------- mijn-installatie.md ----------

def _bekend(cfg, db_pad):
    """Wat het systeem zelf weet (zonder geheimen en zonder adres)."""
    f = lambda s, k: ec.cfg_str(cfg, s, k)
    rij = []
    if ec.demo_actief(cfg):
        rij.append("Meter: nog niet ingesteld (demo-modus)")
    else:
        rij.append("Meter: HomeWizard P1 (lokale API v1), elke 10 s uitgelezen door het HEMS-meetsysteem")
    if f("installatie", "fasen"):
        hz = f("installatie", "hoofdzekering")
        rij.append(f"Aansluiting: {f('installatie', 'fasen')} fase(n), "
                   + (f"hoofdzekering {hz} A" if hz else "hoofdzekering niet ingevuld"))
    land = f("tarief", "land")
    if land:
        plaats = {"BE": "België", "NL": "Nederland"}.get(land, land)
        if f("tarief", "gewest"):
            plaats += ", " + {"VL": "Vlaanderen", "BR": "Brussel", "WA": "Wallonië"}.get(f("tarief", "gewest"), "")
        if f("tarief", "gemeente"):
            plaats += f", gemeente {f('tarief', 'gemeente')}"
        rij.append(f"Ligging: {plaats}")
    if f("tarief", "netgebied"):
        try:
            import ems_netgebieden as ng
            rij.append(f"Netbeheerder: {ng.NETGEBIEDEN.get(f('tarief', 'netgebied'), {}).get('naam', f('tarief', 'netgebied'))}")
        except Exception:
            pass
    soort = f("tarief", "soort")
    if soort:
        lev = f("tarief", "leverancier")
        rij.append("Contract: " + {"enkel": "vaste prijs", "dagnacht": "dag/nacht", "variabel": "variabel (maandindex)",
                                   "dynamisch": "dynamisch (marktprijs per kwartier/uur)",
                                   "blok": "vast blok kWh + daarboven variabel"}.get(soort, soort)
                   + (f", leverancier {lev}" if lev else ", leverancier niet ingevuld"))
        rij += ["  - " + r for r in _contract_details(cfg)]
    try:
        db = sqlite3.connect(f"file:{db_pad}?mode=ro", uri=True, timeout=5)
        r = db.execute("SELECT meter_maandpiek_w, ts FROM metingen WHERE meter_maandpiek_w IS NOT NULL "
                       "ORDER BY ts DESC LIMIT 1").fetchone()
        n = db.execute("SELECT MIN(ts) FROM metingen").fetchone()[0]
        db.close()
        if r and r[0]:
            rij.append(f"Maandpiek deze maand (meter): {ec.kw_w(r[0])}")
        if n:
            rij.append(f"Metingen sinds: {n[:10]}")
    except Exception:
        pass
    url = f("provider", "api_url")
    if url:
        rij.append("Marktprijzen: " + ("ENTSO-E" if "entsoe" in url.lower() else "EnergyZero" if "energyzero" in url.lower() else "ingesteld"))
    return rij


def _getal(x, dec=4):
    """Getal in lokale notatie, zonder overbodige nullen (0,0900 -> 0,09)."""
    t = f"{x:.{dec}f}"
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t.replace(".", ",")


def _contract_details(cfg):
    """De bedragen van het contract zoals ingevuld op de pagina Instellingen (geen geheimen)."""
    def g(k):
        v = ec.cfg_str(cfg, "tarief", k)
        try:
            return float(v.replace(",", ".")) if v != "" else None
        except ValueError:
            return None
    ct = lambda k: None if g(k) is None else _getal(g(k) * 100, 5) + " c€/kWh"
    soort = ec.cfg_str(cfg, "tarief", "soort")
    incl = ec.cfg_str(cfg, "tarief", "bedragen", "excl") == "incl"
    uit = [f"Bedragen ingevuld {'inclusief' if incl else 'exclusief'} btw"
           + (f" (btw {_getal(g('btw_procent'), 2)} %)" if g("btw_procent") is not None else "")]
    formule = lambda: (f"{_getal(g('formule_factor') or 1, 4)} × maandindex + "
                       f"{_getal(g('formule_toeslag_eur_mwh') or 0, 3)} €/MWh")
    if soort in ("enkel", "dagnacht"):
        if soort == "enkel":
            uit.append(f"Energieprijs: {ct('dag_eur_kwh') or 'niet ingevuld'}")
        else:
            uit.append(f"Dag {ct('dag_eur_kwh') or '?'}, nacht {ct('nacht_eur_kwh') or '?'}; dag van "
                       f"{ec.cfg_str(cfg, 'tarief', 'dag_start_uur', '7')} tot {ec.cfg_str(cfg, 'tarief', 'dag_einde_uur', '22')} u, "
                       f"weekend als nacht: {ec.cfg_str(cfg, 'tarief', 'weekend_als_nacht', 'ja')}")
    elif soort == "variabel":
        uit.append(f"Energieprijs: {formule()}")
    elif soort == "dynamisch":
        uit.append(f"Energieprijs: {formule().replace('maandindex', 'marktprijs')}")
    elif soort == "blok":
        uit.append(f"Blok: {_getal(g('blok_kwh_jaar') or 0, 0)} kWh per jaar aan {ct('blok_eur_kwh') or '?'} "
                   f"(verdeeld over de maanden: {ec.cfg_str(cfg, 'tarief', 'blok_verdeling', 'spp')}, "
                   f"ongebruikt blok schuift door: {ec.cfg_str(cfg, 'tarief', 'blok_doorschuiven', 'nee')}); "
                   f"daarboven {formule()}")
    inj = ec.cfg_str(cfg, "tarief", "injectie_soort", "vast")
    if inj == "vast":
        uit.append(f"Injectie: vast {ct('injectie_eur_kwh') or 'niet ingevuld'}")
    else:
        t = (f"Injectie: {_getal(g('injectie_factor') or 1, 4)} × "
             f"{'marktprijs' if inj == 'dynamisch' else 'maandindex'} + {_getal(g('injectie_toeslag_eur_mwh') or 0, 3)} €/MWh")
        if g("injectie_min_eur_kwh") is not None:
            t += f", minimum {ct('injectie_min_eur_kwh')}"
        uit.append(t)
    net = [(n, ct(k)) for n, k in (("distributie", "distributie_eur_kwh"), ("groenestroom", "groenestroom_eur_kwh"),
                                    ("WKK", "wkk_eur_kwh"), ("energiebijdrage", "energiebijdrage_eur_kwh"),
                                    ("accijns", "accijns_eur_kwh"))]
    if any(v for _, v in net):
        uit.append("Netkosten en heffingen per kWh: " + ", ".join(f"{n} {v or 'niet ingevuld'}" for n, v in net))
    vast = []
    if g("capaciteit_eur_kw_jaar") is not None:
        vast.append(f"capaciteitstarief {_getal(g('capaciteit_eur_kw_jaar'), 3)} €/kW/jaar")
    if g("databeheer_eur_jaar") is not None:
        vast.append(f"databeheer {_getal(g('databeheer_eur_jaar'), 2)} €/jaar")
    if g("vast_eur_jaar") is not None:
        vast.append(f"vaste vergoeding leverancier {_getal(g('vast_eur_jaar'), 2)} €/jaar")
    if vast:
        uit.append("Vaste kosten: " + ", ".join(vast))
    return uit


def markdown(cfg, cfgpad, db_pad):
    d = lees(cfgpad)
    map_ = os.path.dirname(os.path.dirname(os.path.abspath(cfgpad)))
    thuis = os.path.expanduser("~")
    if map_ == thuis or map_.startswith(thuis + os.sep):
        map_ = "~" + map_[len(thuis):]
    uit = ["# Mijn installatie", "",
           f"Ingevuld met de vragenlijst van het HEMS-meetsysteem op {datetime.now().strftime('%d/%m/%Y %H:%M')}.",
           "Voor de AI-assistent: dit is het overzicht uit fase 1 van het HEMS-template. Lees het eerst, stel enkel "
           "nog vragen over wat hieronder 'niet ingevuld' is of onduidelijk, en laat de gebruiker het geheel bevestigen. "
           "Wat onder 'Wat het meetsysteem al weet' staat (ook de bedragen van het contract), heeft de gebruiker al "
           "ingevuld op de pagina Instellingen: vraag dat niet opnieuw. "
           f"Het HEMS-meetsysteem (map {map_}, services hems-systeem-*) meet al: bouw daarop verder en wijzig "
           "de bestanden ervan niet (zie het template, fase 3).", "",
           "## Wat het meetsysteem al weet", ""]
    uit += [r if r.startswith("  - ") else f"- {r}" for r in _bekend(cfg, db_pad)] + [""]
    for s in secties():
        if s["id"] in ("vl_klaar", "vl_toestellen_zoeken"):
            continue
        g = d.get(s["id"], {})
        uit += [f"## {s['titel']}", ""]
        lijst = []
        kop = None
        for v in s["velden"]:
            if v["type"] == "kop":
                kop = f"- **{v['label']}**"     # pas tonen als er een aangevinkt item onder staat
                continue
            if v.get("toon"):
                a, b = v["toon"]
                if str(g.get(a, "")) != str(b):
                    continue
            x = g.get(v["id"])
            if v["type"] == "vink":
                if x:
                    if kop:
                        lijst.append(kop)
                        kop = None
                    lijst.append(f"  - {v['label']}" if any(v["id"] == i for i, _ in DOELEN + TOESTELLEN_G) else f"- {v['label']}: ja")
                continue
            if v["type"] == "keuze":
                t = dict((o[0], o[1]) for o in v["opties"]).get(x or "", "")
                x = t if x else ""
            kop = None                           # een gewone vraag sluit de groep af
            lijst.append(f"- {v['label']}: {x if x else '(niet ingevuld)'}")
        uit += lijst + [""]
    gev = d.get("vl_toestellen_zoeken", {}).get("gevonden", [])
    uit += ["## Gevonden op het thuisnetwerk", ""]
    if gev:
        uit += [f"- {t['soort']}: {t['naam']} ({t['detail']}) op {t['ip']}" for t in gev]
        uit += ["", "Thuisbatterijen werden bewust niet gezocht (één-verbinding-limiet); vraag naar hun IP-adres."]
    else:
        uit += ["- Nog niet gezocht."]
    uit += [""]
    return "\n".join(uit)


# ---------- pagina ----------

_JS_EXTRA = r"""
window.EXTRA={
 vl_toestellen_zoeken:c=>{const s=S[actief];const p=maak('div',{class:'paneel'},maak('h2',{},s.icoon+' '+s.titel),maak('p',{class:'intro',html:s.intro}));
  const lijst=maak('div',{class:'kaartlijst',id:'tlijst'});
  const toon=t=>{lijst.innerHTML='';if(!t.length){lijst.append(maak('p',{},'Nog niets gevonden (of nog niet gezocht).'));return}
   for(const x of t)lijst.append(maak('div',{class:'okaart'},maak('div',{},maak('div',{class:'t'},x.soort+': '+x.naam),maak('div',{class:'s'},x.detail+' — '+x.ip))))};
  toon((D.waarden.vl_toestellen_zoeken||{}).gevonden||[]);
  const k=maak('button',{type:'button',class:'hoofd',onclick:async()=>{k.disabled=true;meld('uit',true,'Zoeken op je netwerk… (ca. 10 s)');
   try{const j=await post('/instellingen/vl-zoek',{});toon(j.toestellen||[]);D.waarden.vl_toestellen_zoeken={gevonden:j.toestellen||[]};
    D.overzicht=j.overzicht||D.overzicht;nav();meld('uit',true,(j.toestellen||[]).length+' toestel(len) gevonden. Ze staan nu ook in het bestand.')}
   catch(e){meld('uit',false,'Zoeken mislukt.')}finally{k.disabled=false}}},'🔎 Zoek toestellen');
  p.append(maak('div',{class:'knoppen'},k),lijst,maak('div',{class:'melding',id:'uit'}));c.append(p)},
 vl_klaar:async c=>{const p=maak('div',{class:'paneel'},maak('h2',{},'📄 Klaar: het bestand'),
  maak('p',{class:'intro',html:'Alles wat je invulde, staat in <b>mijn-installatie.md</b> (in je projectmap: <code>'+D.uitvoer+'</code>). '+
   'Zo ga je verder met een AI-assistent:'}),
  maak('ol',{},maak('li',{html:'Download het <a href="https://github.com/luc-vancraen/home-energy-management-system-template" target="_blank" rel="noopener">HEMS-template</a> (hems-template-nl.md).'}),
   maak('li',{},'Zet het template en mijn-installatie.md samen in een lege map.'),
   maak('li',{},'Start je AI-assistent in die map en zeg: "Lees hems-template-nl.md en mijn-installatie.md en volg het template."')),
  maak('div',{class:'knoppen'},maak('a',{href:'/instellingen/vragenlijst/mijn-installatie.md',download:'mijn-installatie.md',class:'knop hoofd'},'⬇ Download mijn-installatie.md')),
  maak('h3',{},'Voorbeeld'),maak('pre',{class:'md',id:'md'},'laden…'));c.append(p);
  try{const r=await fetch('/instellingen/vragenlijst/mijn-installatie.md');$('md').textContent=r.ok?await r.text():'Nog niets ingevuld.'}catch(e){$('md').textContent='Kon het bestand niet laden.'}}
};
"""


def pagina(csrf, cfg, cfgpad, db_pad, stijl_basis):
    import ems_instellingen as ei
    data = {"csrf": csrf, "secties": secties(), "waarden": waarden(cfgpad), "overzicht": overzicht(cfgpad),
            "opslaan_pad": "/instellingen/vl-opslaan", "uitvoer": uitvoer_pad(cfgpad)}
    init = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return ("<!doctype html><html lang='nl'><head><meta charset='utf-8'><title>HEMS — mijn installatie</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{stijl_basis}{ei.STIJL_KIOSK}</style></head><body class='kiosk'>"
            "<h1>HEMS — mijn installatie</h1><p class='nav'><a href='/instellingen#ai'>&larr; terug naar de instellingen</a></p>"
            "<noscript><p class='melding fout'>Deze pagina heeft JavaScript nodig.</p></noscript>"
            "<div class='indeling'><nav class='zij' id='zij'></nav><main class='inhoud' id='inhoud'></main></div>"
            "<p class='voet'>De vragenlijst wijzigt niets aan het meetsysteem of aan een toestel; ze maakt enkel het "
            "bestand mijn-installatie.md. Copyright (c) 2026 luc-vancraen</p>"
            f"<script>const INIT={init};{_JS_EXTRA}{ei._JS}</script>{ec.OOGJE}</body></html>")
