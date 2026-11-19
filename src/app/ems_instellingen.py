"""ems_instellingen - de pagina Instellingen van het dashboard (kioskstijl).

Alle onderdelen staan hieronder als schema: velden, keuzelijsten, uitleg en
controles. De pagina wordt in de browser opgebouwd uit dat schema; opslaan
gebeurt per onderdeel. Er wordt alleen geschreven naar de eigen configuratie
(config/ems.ini, telkens met back-up) en naar .env (geheimen, alleen leesbaar
voor de eigenaar). Er wordt nooit naar een toestel geschreven.
"""
import html
import json
import re
import smtplib
import urllib.error
from datetime import timedelta

import ems_common as ec

ENERGYZERO = "https://api.energyzero.nl/v1/energyprices"
ENTSOE = "https://web-api.tp.entsoe.eu/api"
ZONES = [
    ("BE", "België", "10YBE----------2"),
    ("NL", "Nederland", "10YNL----------L"),
    ("DELU", "Duitsland en Luxemburg", "10Y1001A1001A82H"),
    ("FR", "Frankrijk", "10YFR-RTE------C"),
    ("AT", "Oostenrijk", "10YAT-APG------L"),
]
ZEKERINGEN = [16, 20, 25, 32, 40, 50, 63]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9-]{20,100}$")


class Fout(ValueError):
    """Een fout in wat de gebruiker invulde; de tekst gaat zo naar het scherm."""


# ---------- het schema van de pagina ----------

def _ng():
    import ems_netgebieden
    return ems_netgebieden


def _uren():
    return [[str(u), f"{u:02d}:00"] for u in range(24)]


def _tijdzones(huidig):
    try:
        from zoneinfo import available_timezones
        zones = sorted(t for t in available_timezones() if t.startswith("Europe/"))
    except Exception:
        zones = []
    if huidig and huidig not in zones:
        zones.insert(0, huidig)
    return zones or [huidig or "Europe/Brussels"]


def schema(cfg):
    tz = ec.cfg_str(cfg, "algemeen", "tijdzone", "Europe/Brussels")
    return [
        {"id": "overzicht", "titel": "Overzicht", "icoon": "🏠"},
        {"id": "meter", "titel": "Meter", "icoon": "⚡",
         "intro": "Waar haalt het systeem de metingen? Klik op <b>Zoek mijn meter</b>: het zoekt je P1-meter op je "
                  "thuisnetwerk. Daarna <b>Test</b> en <b>Opslaan</b>.",
         "velden": [
             {"id": "url", "type": "tekst", "label": "Adres van de meter",
              "plaats": "http://192.168.1.50/api/v1/data",
              "hulp": "Wordt ingevuld door 'Zoek mijn meter'. Zelf invullen kan ook; het eindigt meestal op /api/v1/data."},
             {"id": "demo", "type": "keuze", "label": "Demo-modus",
              "opties": [["auto", "Automatisch: demo zolang er geen meter is ingevuld"],
                         ["ja", "Altijd demo (gesimuleerde metingen)"], ["nee", "Nooit demo"]],
              "hulp": "Demo-metingen gaan naar een aparte databank en komen nooit tussen je echte metingen."},
         ]},
        {"id": "tarief", "titel": "Contract en tarief", "icoon": "💶",
         "intro": "Neem de cijfers over van je <b>tariefkaart</b> (te vinden op de website van je leverancier) of je "
                  "<b>factuur</b>. Hoe vollediger, hoe beter de kosten met je factuur overeenkomen. Twijfel je over "
                  "een veld: laat het voorlopig leeg en vergelijk na een maand met je factuur.",
         "velden": [
             {"id": "leverancier", "type": "tekst", "label": "Leverancier en naam van het contract (ter info)",
              "plaats": "bv. EnergyVision, Goedkope stroom 1.000 kWh vast"},
             {"id": "k_energie", "type": "kop", "label": "1. Energie (van je leverancier)"},
             {"id": "soort", "type": "keuze", "label": "Soort contract",
              "opties": [["enkel", "Vaste prijs, één tarief"],
                         ["dagnacht", "Vaste prijs, dag en nacht (tweevoudig)"],
                         ["variabel", "Variabel: formule met een maandindex (bv. 1,05 × Belpex-RLP-M + 15)"],
                         ["dynamisch", "Dynamisch: formule met de marktprijs per kwartier of uur"],
                         ["blok", "Vast blok kWh per jaar + daarboven variabel (bv. EnergyVision 1.000 of 1.800 kWh)"]],
              "hulp": "Staat op je tariefkaart. Bij een formule staat er iets als 'a × index + b'."},
             {"id": "bedragen", "type": "keuze", "label": "De bedragen die je hieronder invult, zijn",
              "opties": [["excl", "Exclusief btw (zoals meestal op de tariefkaart)"],
                         ["incl", "Inclusief btw (zoals op je factuur)"]],
              "hulp": "De teruglevering is altijd zonder btw."},
             {"id": "dag", "type": "getal", "eenheid": "c€/kWh", "label": "Prijs per kWh (dag, of je enige prijs)",
              "toon": ["soort", ['enkel', 'dagnacht']], "plaats": "bv. 12,5", "hulp": "0,125 €/kWh = 12,5 c€/kWh."},
             {"id": "nacht", "type": "getal", "eenheid": "c€/kWh", "label": "Prijs per kWh 's nachts",
              "toon": ["soort", "dagnacht"]},
             {"id": "dag_start", "type": "keuze", "label": "Dagtarief begint om", "opties": _uren(),
              "toon": ["soort", "dagnacht"]},
             {"id": "dag_einde", "type": "keuze", "label": "Dagtarief eindigt om", "opties": _uren(),
              "toon": ["soort", "dagnacht"],
              "hulp": "Staat op je factuur of bij je netbeheerder. Twijfel je: vergelijk na een paar dagen met je "
                      "kwartierdata (in Vlaanderen via 'Mijn Fluvius')."},
             {"id": "weekend", "type": "vink", "label": "Het hele weekend aan nachtprijs", "toon": ["soort", "dagnacht"]},
             {"id": "blok_kwh", "type": "getal", "eenheid": "kWh/jaar", "label": "Grootte van het vaste blok",
              "toon": ["soort", "blok"], "plaats": "bv. 1000"},
             {"id": "blok_prijs", "type": "getal", "eenheid": "c€/kWh", "label": "Prijs van het blok",
              "toon": ["soort", "blok"], "plaats": "bv. 9,00"},
             {"id": "blok_verdeling", "type": "keuze", "label": "Hoe wordt het blok over de maanden verdeeld?",
              "toon": ["soort", "blok"],
              "opties": [["spp", "Volgens het zonneprofiel (SPP): veel in de zomer, weinig in de winter"],
                         ["dagen", "Gelijk, naar het aantal dagen van de maand"]],
              "hulp": "Bij EnergyVision 'op maat van de zon': het zonneprofiel."},
             {"id": "blok_door", "type": "vink", "label": "Ongebruikt blok schuift door naar de volgende maand",
              "toon": ["soort", "blok"], "hulp": "Bij EnergyVision niet: wat je in een maand niet opgebruikt, vervalt die maand."},
             {"id": "factor", "type": "getal", "eenheid": "×", "label": "Factor (a in a × index + b)",
              "toon": ["soort", ['variabel', 'dynamisch', 'blok']], "plaats": "bv. 1,05"},
             {"id": "toeslag", "type": "getal", "eenheid": "€/MWh", "label": "Toeslag (b in a × index + b)",
              "toon": ["soort", ['variabel', 'dynamisch', 'blok']], "plaats": "bv. 15",
              "hulp": "In €/MWh, zoals op de tariefkaart. 1,5 c€/kWh = 15 €/MWh."},
             {"id": "k_injectie", "type": "kop", "label": "2. Teruglevering"},
             {"id": "inj_soort", "type": "keuze", "label": "Vergoeding voor teruglevering",
              "opties": [["vast", "Vaste prijs per kWh"],
                         ["formule", "Formule met een maandindex (bv. 0,6 × Belpex-SPP-M − 15)"],
                         ["dynamisch", "Formule met de marktprijs per kwartier of uur"]]},
             {"id": "injectie", "type": "getal", "eenheid": "c€/kWh", "label": "Vergoeding per kWh",
              "toon": ["inj_soort", "vast"], "hulp": "Geen zonnepanelen: 0."},
             {"id": "inj_factor", "type": "getal", "eenheid": "×", "label": "Factor",
              "toon": ["inj_soort", ["formule", "dynamisch"]], "plaats": "bv. 0,6"},
             {"id": "inj_toeslag", "type": "getal", "eenheid": "€/MWh", "label": "Toeslag (meestal negatief)",
              "toon": ["inj_soort", ["formule", "dynamisch"]], "plaats": "bv. -15"},
             {"id": "inj_min", "type": "getal", "eenheid": "c€/kWh", "label": "Minimum (leeg = geen)",
              "toon": ["inj_soort", ["formule", "dynamisch"]], "plaats": "bv. 1", "leeg_mag": True},
             {"id": "k_net", "type": "kop", "label": "3. Netkosten, heffingen en vaste kosten",
              "hulp": "De netkosten hangen af van waar je woont. In Vlaanderen vult het systeem ze zelf in zodra je je "
                      "gemeente kiest (officiële tarieven 2026 van Fluvius). De heffingen en de vaste vergoeding "
                      "staan op je factuur of tariefkaart."},
             {"id": "land", "type": "keuze", "label": "Land",
              "opties": [["BE", "België"], ["NL", "Nederland"], ["ander", "Ander land"]]},
             {"id": "gewest", "type": "keuze", "label": "Gewest", "toon": ["land", "BE"],
              "opties": [["VL", "Vlaanderen"], ["BR", "Brussels Hoofdstedelijk Gewest"], ["WA", "Wallonië"]]},
             {"id": "gemeente", "type": "keuze", "label": "Gemeente", "toon": [["land", "BE"], ["gewest", "VL"]],
              "opties": [["", "— kies je gemeente —"]] + [[g, g] for g in sorted(_ng().GEMEENTEN)],
              "hulp": "Bepaalt je netbeheerder en dus je netkosten."},
             {"id": "netgebied", "type": "keuze", "label": "Netbeheerder (netgebied)", "toon": [["land", "BE"], ["gewest", "VL"]],
              "opties": [["", "— onbekend —"]] + [[k, v["naam"]] for k, v in _ng().NETGEBIEDEN.items()],
              "hulp": "Wordt ingevuld als je je gemeente kiest; staat ook op je factuur."},
             {"id": "i_elders", "type": "info", "toon": ["land", ["NL", "ander"]],
              "label": "Voor dit land vult het systeem de netkosten (nog) niet zelf in: neem ze over van je factuur. "
                       "Zet de btw op het tarief van je land (Nederland: 21 %)."},
             {"id": "i_brwa", "type": "info", "toon": [["land", "BE"], ["gewest", ["BR", "WA"]]],
              "label": "Voor Brussel en Wallonië vult het systeem de netkosten (nog) niet zelf in: neem ze over van je "
                       "factuur (Sibelga, ORES, RESA, ...). Het capaciteitstarief geldt alleen in Vlaanderen: laat dat veld leeg."},
             {"id": "distributie", "type": "getal", "eenheid": "c€/kWh", "label": "Distributie (afnametarief)", "leeg_mag": True},
             {"id": "groenestroom", "type": "getal", "eenheid": "c€/kWh", "label": "Groenestroomcertificaten", "leeg_mag": True,
              "hulp": "In Vlaanderen vooraf ingevuld met een gangbare waarde. Het juiste bedrag verschilt per leverancier: "
                      "het staat op je tariefkaart of factuur, meestal onder 'Bijdrage groene stroom' of 'Kosten groene "
                      "stroom en WKK'."},
             {"id": "wkk", "type": "getal", "eenheid": "c€/kWh", "label": "Warmtekrachtcertificaten (WKK)", "leeg_mag": True,
              "hulp": "Zoals hierboven: vooraf ingevuld, controleer op je tariefkaart."},
             {"id": "energiebijdrage", "type": "getal", "eenheid": "c€/kWh", "label": "Energiebijdrage", "leeg_mag": True,
              "hulp": "Federaal, voor heel België hetzelfde: wordt automatisch ingevuld."},
             {"id": "accijns", "type": "getal", "eenheid": "c€/kWh", "label": "Bijzondere accijns", "leeg_mag": True,
              "hulp": "Federaal, voor gezinnen in heel België hetzelfde: wordt automatisch ingevuld."},
             {"id": "capaciteit", "type": "getal", "eenheid": "€/kW/jaar", "label": "Capaciteitstarief", "leeg_mag": True,
              "hulp": "Wordt gerekend op het gemiddelde van je maandpieken (laatste 12 maanden), elke maand minstens "
                      "het minimum uit 'Installatie'. Het systeem meet die pieken zelf."},
             {"id": "databeheer", "type": "getal", "eenheid": "€/jaar", "label": "Databeheer (netbeheerder)", "leeg_mag": True},
             {"id": "vast", "type": "getal", "eenheid": "€/jaar", "label": "Vaste vergoeding van de leverancier", "leeg_mag": True},
             {"id": "btw", "type": "getal", "eenheid": "%", "label": "Btw", "hulp": "In België 6 % voor elektriciteit thuis."},
         ]},
        {"id": "prijzen", "titel": "Marktprijzen", "icoon": "📈",
         "intro": "Optioneel. De marktprijzen per uur of kwartier (day-ahead), elke middag voor de volgende dag. "
                  "Handig bij een dynamisch contract of om te vergelijken.",
         "velden": [
             {"id": "bron", "type": "keuze", "label": "Bron",
              "opties": [["geen", "Geen marktprijzen"],
                         ["entsoe", "ENTSO-E: officiële Europese marktprijzen (gratis token nodig)"],
                         ["energyzero", "EnergyZero: Nederlandse prijzen, zonder token"]]},
             {"id": "land", "type": "keuze", "label": "Land (prijszone)", "toon": ["bron", "entsoe"],
              "opties": [[c, n] for c, n, _ in ZONES]},
             {"id": "token", "type": "geheim", "label": "ENTSO-E-token", "toon": ["bron", "entsoe"],
              "hulp": "Aanvragen: maak een account op transparency.entsoe.eu en mail naar transparency@entsoe.eu met "
                      "als onderwerp 'Restful API access'. Na hun bevestiging vind je de token onder My Account "
                      "Settings → Web API Security Token. De token komt alleen in .env, nooit op het scherm."},
         ]},
        {"id": "installatie", "titel": "Installatie", "icoon": "🔌",
         "intro": "Gegevens van je aansluiting. Ze worden gebruikt voor de bewaking en het capaciteitstarief.",
         "velden": [
             {"id": "fasen", "type": "keuze", "label": "Aantal fasen",
              "opties": [["1", "1 fase (enkelfasig)"], ["3", "3 fasen (driefasig)"]],
              "hulp": "Weet je het niet? Gebruik de knop hieronder: de meter weet het."},
             {"id": "hoofdzekering", "type": "keuze", "label": "Hoofdzekering",
              "opties": [[str(a), f"{a} A"] for a in ZEKERINGEN],
              "hulp": "Staat op de hoofdschakelaar bij je meter, of op je aansluitingscontract."},
             {"id": "minimum_piek_kw", "type": "getal", "eenheid": "kW", "label": "Minimum voor het capaciteitstarief",
              "hulp": "In Vlaanderen tellen pieken onder 2,5 kW niet mee. Laat 2,5 staan tenzij je netbeheerder iets "
                      "anders zegt."},
         ]},
        {"id": "mail", "titel": "Dagrapport per mail", "icoon": "✉️",
         "intro": "Optioneel. Elke ochtend het rapport van gisteren in je mailbox.",
         "velden": [
             {"id": "aan", "type": "vink", "label": "Dagrapport elke ochtend mailen"},
             {"id": "aanbieder", "type": "keuze", "label": "Mailaanbieder", "toon": ["aan", True],
              "opties": [["gmail", "Gmail"], ["ander", "Andere mailserver"]]},
             {"id": "smtp_host", "type": "tekst", "label": "Mailserver (SMTP)", "toon": ["aan", True],
              "plaats": "bv. smtp.gmail.com"},
             {"id": "smtp_poort", "type": "keuze", "label": "Poort", "toon": ["aan", True],
              "opties": [["465", "465 (SSL)"], ["587", "587 (STARTTLS)"]]},
             {"id": "gebruiker", "type": "tekst", "label": "Gebruikersnaam (meestal je e-mailadres)",
              "toon": ["aan", True]},
             {"id": "wachtwoord", "type": "geheim", "label": "Wachtwoord van de mailbox", "toon": ["aan", True],
              "hulp": "Bij Gmail: een app-wachtwoord (Google-account → Beveiliging → App-wachtwoorden; "
                      "tweestapsverificatie moet aan staan), niet je gewone wachtwoord. Komt alleen in .env."},
             {"id": "naar", "type": "tekst", "label": "Rapport sturen naar", "toon": ["aan", True],
              "plaats": "naam@voorbeeld.be"},
             {"id": "van", "type": "tekst", "label": "Afzender (leeg = gebruikersnaam)", "toon": ["aan", True]},
         ]},
        {"id": "dashboard", "titel": "Dashboard", "icoon": "🖥️",
         "intro": "Wie mag het dashboard bekijken, en op welk adres.",
         "velden": [
             {"id": "beveiligen", "type": "vink", "label": "Dashboard afschermen met een wachtwoord",
              "hulp": "Aanraden als anderen op je netwerk zitten (gasten-wifi, huisgenoten): je verbruik toont "
                      "wanneer je thuis bent."},
             {"id": "kijkwachtwoord", "type": "geheim", "label": "Wachtwoord om te bekijken (minstens 8 tekens)",
              "toon": ["beveiligen", True],
              "hulp": "De browser vraagt het één keer; de gebruikersnaam mag je leeg laten."},
             {"id": "poort", "type": "getal", "label": "Poort",
              "hulp": "Standaard 8080. Wijzigen herstart het dashboard; je wordt daarna doorgestuurd."},
             {"id": "bind", "type": "keuze", "label": "Bereikbaar vanaf",
              "opties": [["0.0.0.0", "Het hele thuisnetwerk (gsm, andere pc's)"],
                         ["127.0.0.1", "Alleen deze pc"]],
              "hulp": "Kies je 'Alleen deze pc', dan kan je het dashboard niet meer vanaf een ander toestel openen."},
         ]},
        {"id": "algemeen", "titel": "Algemeen", "icoon": "⚙️",
         "velden": [
             {"id": "tijdzone", "type": "keuze", "label": "Tijdzone",
              "opties": [[z, z.replace("_", " ")] for z in _tijdzones(tz)],
              "hulp": "Bepaalt de uren op het dashboard en de grenzen van dagen en dagtarief."},
         ]},
        {"id": "ai", "titel": "Later sturing met AI", "icoon": "🤖", "velden": []},
        {"id": "wachtwoord", "titel": "Beheerwachtwoord", "icoon": "🔑"},
    ]


# ---------- huidige waarden (zonder geheimen: enkel of ze ingesteld zijn) ----------

def _env(cfgpad):
    return ec.lees_env(ec.pad_in_project(cfgpad, ".env"))


def _getal_tekst(x):
    s = f"{x:.5f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def waarden(cfg, cfgpad):
    env = _env(cfgpad)
    url_p = ec.cfg_str(cfg, "provider", "api_url").lower()
    bron = "entsoe" if "entsoe" in url_p else ("energyzero" if "energyzero" in url_p else ("geen" if not url_p else "entsoe"))
    gebied = ec.cfg_str(cfg, "provider", "gebied", ZONES[0][2])
    land = next((c for c, _, z in ZONES if z == gebied), "BE")
    dag = ec.cfg_float(cfg, "tarief", "dag_eur_kwh", 0.0)
    nacht = ec.cfg_float(cfg, "tarief", "nacht_eur_kwh", 0.0)
    soort = ec.cfg_str(cfg, "tarief", "soort", "")
    if soort not in ("enkel", "dagnacht"):
        soort = "enkel" if (dag == nacht and dag > 0) else "dagnacht"
    demo = ec.cfg_str(cfg, "meter", "demo", "auto").lower()
    demo = {"1": "ja", "j": "ja", "yes": "ja", "true": "ja", "0": "nee", "n": "nee", "no": "nee", "false": "nee"}.get(demo, demo)
    host = ec.cfg_str(cfg, "mail", "smtp_host")
    poort_mail = str(ec.cfg_int(cfg, "mail", "smtp_poort", 465))
    fasen = str(ec.cfg_int(cfg, "installatie", "fasen", 3))
    zekering = str(ec.cfg_int(cfg, "installatie", "hoofdzekering_a", 25))
    return {
        "meter": {"url": ec.cfg_str(cfg, "meter", "url"), "demo": demo if demo in ("auto", "ja", "nee") else "auto"},
        "tarief": _tarief_waarden(cfg),
        "prijzen": {"bron": bron, "land": land, "token": bool(env.get("HEMS_PROVIDER_TOKEN"))},
        "installatie": {"fasen": fasen if fasen in ("1", "3") else "3",
                        "hoofdzekering": zekering if int(zekering) in ZEKERINGEN else "25",
                        "minimum_piek_kw": _getal_tekst(ec.cfg_float(cfg, "installatie", "minimum_piek_kw", 2.5))},
        "mail": {"aan": ec.cfg_bool(cfg, "mail", "aan", False),
                 "aanbieder": "gmail" if host == "smtp.gmail.com" or not host else "ander",
                 "smtp_host": host, "smtp_poort": poort_mail if poort_mail in ("465", "587") else "465",
                 "gebruiker": ec.cfg_str(cfg, "mail", "gebruiker"), "naar": ec.cfg_str(cfg, "mail", "naar"),
                 "van": ec.cfg_str(cfg, "mail", "van"), "wachtwoord": bool(env.get("HEMS_MAIL_WACHTWOORD"))},
        "dashboard": {"beveiligen": dashboard_beveiligd(cfg, cfgpad),
                      "kijkwachtwoord": dashboard_beveiligd(cfg, cfgpad),
                      "poort": str(ec.cfg_int(cfg, "dashboard", "poort", 8080)),
                      "bind": ec.cfg_str(cfg, "dashboard", "bind", "0.0.0.0")},
        "algemeen": {"tijdzone": ec.cfg_str(cfg, "algemeen", "tijdzone", "Europe/Brussels")},
    }


def _tarief_waarden(cfg):
    import ems_kosten
    c = ems_kosten.contract(cfg)
    rauw = lambda k, d=0.0: ec.cfg_float(cfg, "tarief", k, d)
    cent = lambda k, d=0.0: _getal_tekst(rauw(k, d) * 100) if rauw(k, d) else ""
    leeg = lambda k: _getal_tekst(rauw(k)) if rauw(k) else ""
    inj_min = ec.cfg_str(cfg, "tarief", "injectie_min_eur_kwh", "")
    try:
        inj_min = _getal_tekst(float(inj_min.replace(",", ".")) * 100) if inj_min else ""
    except ValueError:
        inj_min = ""
    return {
        "leverancier": ec.cfg_str(cfg, "tarief", "leverancier"), "soort": c["soort"],
        "bedragen": "incl" if c["incl"] else "excl",
        "dag": cent("dag_eur_kwh"), "nacht": cent("nacht_eur_kwh"),
        "dag_start": str(c["dag_start"]), "dag_einde": str(c["dag_einde"]), "weekend": c["weekend_nacht"],
        "blok_kwh": leeg("blok_kwh_jaar"), "blok_prijs": cent("blok_eur_kwh"),
        "blok_verdeling": c["blok_verdeling"] if c["blok_verdeling"] in ("spp", "dagen") else "spp",
        "blok_door": c["blok_door"],
        "factor": _getal_tekst(rauw("formule_factor", 1.0)), "toeslag": _getal_tekst(rauw("formule_toeslag_eur_mwh")),
        "inj_soort": c["inj_soort"] if c["inj_soort"] in ("vast", "formule", "dynamisch") else "vast",
        "injectie": _getal_tekst(rauw("injectie_eur_kwh") * 100),
        "inj_factor": _getal_tekst(rauw("injectie_factor", 1.0)), "inj_toeslag": _getal_tekst(rauw("injectie_toeslag_eur_mwh")),
        "inj_min": inj_min,
        "distributie": cent("distributie_eur_kwh"), "groenestroom": cent("groenestroom_eur_kwh"),
        "wkk": cent("wkk_eur_kwh"), "energiebijdrage": cent("energiebijdrage_eur_kwh"), "accijns": cent("accijns_eur_kwh"),
        "capaciteit": leeg("capaciteit_eur_kw_jaar"), "databeheer": leeg("databeheer_eur_jaar"), "vast": leeg("vast_eur_jaar"),
        "btw": _getal_tekst(c["btw"]),
        "land": ec.cfg_str(cfg, "tarief", "land", "BE"), "gewest": ec.cfg_str(cfg, "tarief", "gewest", "VL"),
        "gemeente": ec.cfg_str(cfg, "tarief", "gemeente"), "netgebied": ec.cfg_str(cfg, "tarief", "netgebied"),
    }


def dashboard_beveiligd(cfg, cfgpad):
    return bool(_env(cfgpad).get("HEMS_DASHBOARD_WACHTWOORD") or ec.cfg_str(cfg, "dashboard", "wachtwoord"))


# ---------- overzicht: wat is in orde, wat vraagt aandacht ----------

def fasen_uit_meter(db_pad):
    try:
        db = ec.open_db(db_pad)
        r = db.execute("SELECT l1_w, l2_w, l3_w FROM metingen ORDER BY ts DESC LIMIT 1").fetchone()
        db.close()
    except Exception:
        return None
    if not r or r[0] is None:
        return None
    return 3 if (r[1] is not None or r[2] is not None) else 1


def overzicht(cfg, cfgpad, db_pad):
    tz = ec.tijdzone(cfg)
    uit = []

    def zet(id_, staat, tekst):
        uit.append({"id": id_, "staat": staat, "tekst": tekst})

    laatste = None
    try:
        db = ec.open_db(db_pad)
        laatste = db.execute("SELECT ts, vermogen_w FROM metingen ORDER BY ts DESC LIMIT 1").fetchone()
        pr = db.execute("SELECT MAX(start_utc) FROM prijzen").fetchone()
        db.close()
    except Exception:
        pr = None
    if ec.demo_actief(cfg):
        zet("meter", "let", "Demo: nog geen echte meter ingesteld")
    elif not ec.cfg_str(cfg, "meter", "url"):
        zet("meter", "let", "Geen meter ingesteld")
    elif laatste and (ec.utc_nu() - ec.parse_iso(laatste[0])).total_seconds() < 60:
        zet("meter", "ok", f"Meet: nu {ec.kw_w(abs(laatste[1] or 0))} {'teruglevering' if (laatste[1] or 0) < 0 else 'afname'}")
    else:
        zet("meter", "let", "Geen verse metingen: is de meter bereikbaar?")

    w = waarden(cfg, cfgpad)
    import ems_kosten
    c = ems_kosten.contract(cfg)
    namen = {"enkel": "vaste prijs", "dagnacht": "dag/nacht", "variabel": "variabel", "dynamisch": "dynamisch",
             "blok": f"blok {_getal_tekst(c['blok_kwh'])} kWh + variabel"}
    if not (c["dag"] or c["blok_prijs"] or c["soort"] in ("variabel", "dynamisch")):
        zet("tarief", "let", "Nog niet ingevuld: de kosten kloppen niet")
    elif ems_kosten.heeft_marktprijzen_nodig(c) and not ec.cfg_str(cfg, "provider", "api_url"):
        zet("tarief", "let", "Je contract heeft marktprijzen nodig: zet ze aan bij Marktprijzen")
    elif ec.cfg_str(cfg, "tarief", "netgebied") and ec.utc_nu().astimezone(tz).date().isoformat() > _ng().GELDIG_TOT:
        zet("tarief", "let", f"De nettarieven gelden tot {_ng().GELDIG_TOT[8:]}/{_ng().GELDIG_TOT[5:7]}/{_ng().GELDIG_TOT[:4]}: "
                             "vul de nieuwe in of werk het systeem bij")
    elif not c["net_kwh"]:
        zet("tarief", "let", f"{namen[c['soort']]}; netkosten en heffingen nog niet ingevuld")
    else:
        zet("tarief", "ok", namen[c["soort"]] + (f" · {ec.cfg_str(cfg, 'tarief', 'leverancier')}"
                                                 if ec.cfg_str(cfg, "tarief", "leverancier") else ""))

    p = w["prijzen"]
    if p["bron"] == "geen":
        zet("prijzen", "opt", "Uit (optioneel)")
    elif p["bron"] == "entsoe" and not p["token"]:
        zet("prijzen", "let", "ENTSO-E-token ontbreekt")
    elif pr and pr[0] and ec.parse_iso(pr[0]) >= ec.utc_nu() - timedelta(hours=1):
        zet("prijzen", "ok", "Prijzen tot " + ec.parse_iso(pr[0]).astimezone(tz).strftime("%d/%m %H:%M"))
    else:
        zet("prijzen", "let", "Nog geen actuele prijzen opgehaald")

    i = w["installatie"]
    f_meter = fasen_uit_meter(db_pad) if not ec.demo_actief(cfg) else None
    if f_meter and str(f_meter) != i["fasen"]:
        zet("installatie", "let", f"De meter meldt {f_meter} fase(n), ingesteld: {i['fasen']}")
    else:
        zet("installatie", "ok", f"{i['fasen']} fase{'n' if i['fasen'] == '3' else ''} · {i['hoofdzekering']} A")

    m = w["mail"]
    if m["aan"]:
        zet("mail", "ok", "Aan, naar " + (m["naar"] or m["gebruiker"]))
    else:
        zet("mail", "opt", "Uit (optioneel)")

    d = w["dashboard"]
    waar = "alleen deze pc" if d["bind"] == "127.0.0.1" else "thuisnetwerk"
    zet("dashboard", "ok" if d["beveiligen"] else "opt",
        ("Met wachtwoord" if d["beveiligen"] else "Zonder wachtwoord") + f" · {waar} · poort {d['poort']}")
    zet("algemeen", "ok", w["algemeen"]["tijdzone"])
    return uit


# ---------- opslaan per onderdeel ----------

def _getal(w, sleutel, laag, hoog, naam):
    rauw = str(w.get(sleutel, "")).strip().replace(" ", "").replace(",", ".")
    if rauw == "":
        raise Fout(f"Vul '{naam}' in.")
    try:
        x = float(rauw)
    except ValueError:
        raise Fout(f"'{naam}' moet een getal zijn.")
    if not laag <= x <= hoog:
        raise Fout(f"'{naam}' moet tussen {_getal_tekst(laag)} en {_getal_tekst(hoog)} liggen.")
    return x


def _keuze(w, sleutel, toegelaten, naam):
    v = str(w.get(sleutel, "")).strip()
    if v not in toegelaten:
        raise Fout(f"Kies een geldige waarde voor '{naam}'.")
    return v


def _eur(x):
    return f"{x:.7f}".rstrip("0").rstrip(".") or "0"


def opslaan(sectie, w, cfg, cfgpad, db_pad):
    """Valideer en bewaar één onderdeel. Geeft een dict met ok/bericht of ok=False/fout."""
    try:
        return _opslaan(sectie, w if isinstance(w, dict) else {}, cfg, cfgpad, db_pad)
    except Fout as e:
        return {"ok": False, "fout": str(e)}


def _opslaan(sectie, w, cfg, cfgpad, db_pad):
    envpad = ec.pad_in_project(cfgpad, ".env")
    env = ec.lees_env(envpad)
    bericht = "Opgeslagen."
    extra = {}

    if sectie == "meter":
        url = str(w.get("url", "")).strip()
        demo = _keuze(w, "demo", ("auto", "ja", "nee"), "Demo-modus")
        if url:
            if any(c.isspace() for c in url) or "%" in url:
                raise Fout("Het adres mag geen spaties of %-tekens bevatten.")
            t = ec.test_meter(url)
            if not t["ok"] and t["fout"].startswith(("Het adres moet", "Alleen adressen")):
                raise Fout(t["fout"])
            if not t["ok"]:
                bericht += " Let op: de test lukt nu niet (" + t["fout"] + ")"
        elif demo == "nee":
            raise Fout("Zonder meteradres kan 'Nooit demo' niet: er is dan niets om te meten.")
        ec.zet_config(cfgpad, {("meter", "url"): url, ("meter", "demo"): demo})
        bericht += " Het meetprogramma neemt het binnen ca. 10 s over."

    elif sectie == "tarief":
        import ems_kosten
        soort = _keuze(w, "soort", ems_kosten.SOORTEN, "Soort contract")
        bedragen = _keuze(w, "bedragen", ("excl", "incl"), "Bedragen")
        inj_soort = _keuze(w, "inj_soort", ("vast", "formule", "dynamisch"), "Vergoeding voor teruglevering")
        lev = str(w.get("leverancier", "")).strip()
        if "%" in lev or "\n" in lev or re.search(r"\s[#;]", lev) or len(lev) > 100:
            raise Fout("De naam van de leverancier mag geen %, # of ; bevatten (max. 100 tekens).")

        def opt(k, laag, hoog, naam):          # leeg = 0
            return _getal(w, k, laag, hoog, naam) if str(w.get(k, "")).strip() else 0.0
        z = {("tarief", "soort"): soort, ("tarief", "bedragen"): bedragen, ("tarief", "leverancier"): lev,
             ("tarief", "injectie_soort"): inj_soort,
             ("tarief", "btw_procent"): _eur(_getal(w, "btw", 0, 25, "Btw"))}
        if soort in ("enkel", "dagnacht"):
            dag = _getal(w, "dag", 0, 200, "Prijs per kWh")
            nacht = dag if soort == "enkel" else _getal(w, "nacht", 0, 200, "Prijs 's nachts")
            z.update({("tarief", "dag_eur_kwh"): _eur(dag / 100), ("tarief", "nacht_eur_kwh"): _eur(nacht / 100)})
            if soort == "dagnacht":
                start = int(_keuze(w, "dag_start", [str(u) for u in range(24)], "Dagtarief begint om"))
                einde = int(_keuze(w, "dag_einde", [str(u) for u in range(24)], "Dagtarief eindigt om"))
                if start >= einde:
                    raise Fout("Het dagtarief moet beginnen vóór het eindigt (bv. 07:00 tot 22:00).")
                z.update({("tarief", "dag_start_uur"): str(start), ("tarief", "dag_einde_uur"): str(einde),
                          ("tarief", "weekend_als_nacht"): "ja" if w.get("weekend") else "nee"})
        else:
            z.update({("tarief", "formule_factor"): _eur(_getal(w, "factor", 0, 5, "Factor")),
                      ("tarief", "formule_toeslag_eur_mwh"): _eur(_getal(w, "toeslag", -200, 500, "Toeslag"))})
            if soort == "blok":
                z.update({("tarief", "blok_kwh_jaar"): _eur(_getal(w, "blok_kwh", 1, 100000, "Grootte van het blok")),
                          ("tarief", "blok_eur_kwh"): _eur(_getal(w, "blok_prijs", 0, 200, "Prijs van het blok") / 100),
                          ("tarief", "blok_verdeling"): _keuze(w, "blok_verdeling", ("spp", "dagen"), "Verdeling"),
                          ("tarief", "blok_doorschuiven"): "ja" if w.get("blok_door") else "nee"})
        if inj_soort == "vast":
            z[("tarief", "injectie_eur_kwh")] = _eur(_getal(w, "injectie", -50, 100, "Vergoeding per kWh") / 100)
        else:
            z.update({("tarief", "injectie_factor"): _eur(_getal(w, "inj_factor", 0, 5, "Factor teruglevering")),
                      ("tarief", "injectie_toeslag_eur_mwh"): _eur(_getal(w, "inj_toeslag", -500, 500, "Toeslag teruglevering"))})
            m = str(w.get("inj_min", "")).strip()
            z[("tarief", "injectie_min_eur_kwh")] = _eur(_getal(w, "inj_min", -50, 50, "Minimum") / 100) if m else ""
        land = _keuze(w, "land", ("BE", "NL", "ander"), "Land")
        gewest = _keuze(w, "gewest", ("VL", "BR", "WA"), "Gewest") if land == "BE" else ""
        # Leeg gelaten federale heffingen (België) en certificaten (Vlaanderen) aanvullen met de standaardwaarden,
        # omgerekend naar incl. btw als de bedragen zo ingevoerd worden.
        standaard = dict(_ng().HEFFINGEN_BE) if land == "BE" else {}
        if gewest == "VL":
            standaard.update(_ng().CERTIFICATEN_VL)
        btw_f = 1 + _getal(w, "btw", 0, 25, "Btw") / 100 if bedragen == "incl" else 1.0
        for k, cfgk in (("distributie", "distributie_eur_kwh"), ("groenestroom", "groenestroom_eur_kwh"), ("wkk", "wkk_eur_kwh"),
                        ("energiebijdrage", "energiebijdrage_eur_kwh"), ("accijns", "accijns_eur_kwh")):
            if not str(w.get(k, "")).strip() and cfgk in standaard:
                z[("tarief", cfgk)] = _eur(standaard[cfgk] * btw_f)
            else:
                z[("tarief", cfgk)] = _eur(opt(k, 0, 100, k) / 100)
        gemeente = str(w.get("gemeente", "")).strip() if gewest == "VL" else ""
        netgebied = str(w.get("netgebied", "")).strip() if gewest == "VL" else ""
        if gemeente:
            if gemeente not in _ng().GEMEENTEN:
                raise Fout("Onbekende gemeente.")
            netgebied = _ng().GEMEENTEN[gemeente]
        if netgebied and netgebied not in _ng().NETGEBIEDEN:
            raise Fout("Onbekend netgebied.")
        z.update({("tarief", "land"): land, ("tarief", "gewest"): gewest, ("tarief", "gemeente"): gemeente,
                  ("tarief", "netgebied"): netgebied})
        z[("tarief", "capaciteit_eur_kw_jaar")] = _eur(opt("capaciteit", 0, 1000, "Capaciteitstarief"))
        z[("tarief", "databeheer_eur_jaar")] = _eur(opt("databeheer", 0, 1000, "Databeheer"))
        z[("tarief", "vast_eur_jaar")] = _eur(opt("vast", 0, 5000, "Vaste vergoeding"))
        ec.zet_config(cfgpad, z)
        nieuw = ec.laad_config(cfgpad)
        c = ems_kosten.contract(nieuw)
        if ems_kosten.heeft_marktprijzen_nodig(c) and not ec.cfg_str(nieuw, "provider", "api_url"):
            bericht += " Let op: dit contract rekent met marktprijzen; zet die aan bij 'Marktprijzen'."
        try:                                   # meteen tonen wat het betekent, met gisteren als voorbeeld
            tz = ec.tijdzone(nieuw)
            gisteren = (ec.utc_nu().astimezone(tz) - timedelta(days=1)).date()
            db = ec.open_db(db_pad)
            k = ems_kosten.kosten_dag(db, gisteren, tz, nieuw)
            n = db.execute("SELECT COUNT(*) FROM metingen WHERE ts>=? AND ts<?",
                           tuple(ec.iso(x) for x in ec.dag_grenzen_utc(gisteren, tz))).fetchone()[0]
            db.close()
            if n > 100:
                bericht += (f" Voorbeeld: gisteren ({gisteren.strftime('%d/%m')}) kostte alles samen "
                            f"{ec.euro(k['totaal_eur'])} (afname {ec.komma(k['afname_kwh'], 1)} kWh, teruglevering "
                            f"{ec.komma(k['injectie_kwh'], 1)} kWh). De opbouw staat op het dashboard.")
        except Exception:
            pass

    elif sectie == "prijzen":
        bron = _keuze(w, "bron", ("geen", "entsoe", "energyzero"), "Bron")
        if bron == "geen":
            ec.zet_config(cfgpad, {("provider", "api_url"): ""})
            bericht = "Opgeslagen: geen marktprijzen."
        elif bron == "energyzero":
            ec.zet_config(cfgpad, {("provider", "api_url"): ENERGYZERO})
            bericht += " Let op: dit zijn Nederlandse prijzen."
        else:
            land = _keuze(w, "land", [c for c, _, _ in ZONES], "Land")
            token = str(w.get("token", "")).strip()
            if token:
                if not _TOKEN_RE.match(token):
                    raise Fout("Die token ziet er niet goed uit (verwacht: letters, cijfers en streepjes, zoals "
                                "1a2b3c4d-...). Kopieer hem opnieuw uit je ENTSO-E-account.")
                ec.zet_env(envpad, "HEMS_PROVIDER_TOKEN", token)
            elif not env.get("HEMS_PROVIDER_TOKEN"):
                raise Fout("Vul je ENTSO-E-token in (uitleg onder het veld).")
            ec.zet_config(cfgpad, {("provider", "api_url"): ENTSOE,
                                   ("provider", "gebied"): next(z for c, _, z in ZONES if c == land)})
        if bron != "geen":
            bericht += " Klik op 'Test en haal de prijzen op' om te controleren."

    elif sectie == "installatie":
        fasen = _keuze(w, "fasen", ("1", "3"), "Aantal fasen")
        zek = _keuze(w, "hoofdzekering", [str(a) for a in ZEKERINGEN], "Hoofdzekering")
        minimum = _getal(w, "minimum_piek_kw", 0, 10, "Minimum voor het capaciteitstarief")
        ec.zet_config(cfgpad, {("installatie", "fasen"): fasen, ("installatie", "hoofdzekering_a"): zek,
                               ("installatie", "minimum_piek_kw"): _eur(minimum)})

    elif sectie == "mail":
        aan = bool(w.get("aan"))
        host = str(w.get("smtp_host", "")).strip()
        poort = _keuze(w, "smtp_poort", ("465", "587"), "Poort") if aan else str(w.get("smtp_poort") or "465")
        gebruiker = str(w.get("gebruiker", "")).strip()
        naar = str(w.get("naar", "")).strip()
        van = str(w.get("van", "")).strip()
        ww = str(w.get("wachtwoord", ""))
        if aan:
            if not host or not _HOST_RE.match(host):
                raise Fout("Vul een geldige mailserver in (bv. smtp.gmail.com).")
            if not gebruiker or any(c.isspace() for c in gebruiker):
                raise Fout("Vul de gebruikersnaam in (meestal je e-mailadres).")
            if not _EMAIL_RE.match(naar):
                raise Fout("Vul een geldig e-mailadres in bij 'Rapport sturen naar'.")
            if van and not _EMAIL_RE.match(van):
                raise Fout("De afzender moet een e-mailadres zijn (of leeg).")
            if not ww and not env.get("HEMS_MAIL_WACHTWOORD"):
                raise Fout("Vul het wachtwoord van de mailbox in.")
        for x in (host, gebruiker, naar, van):
            if x and (any(c.isspace() for c in x) or "%" in x):
                raise Fout("Mailgegevens mogen geen spaties of %-tekens bevatten.")
        if ww:
            ec.zet_env(envpad, "HEMS_MAIL_WACHTWOORD", ww)
        ec.zet_config(cfgpad, {("mail", "aan"): "ja" if aan else "nee", ("mail", "smtp_host"): host,
                               ("mail", "smtp_poort"): poort, ("mail", "gebruiker"): gebruiker,
                               ("mail", "naar"): naar, ("mail", "van"): van})
        if aan:
            bericht += " Klik op 'Testmail sturen' om te controleren."

    elif sectie == "dashboard":
        bev = bool(w.get("beveiligen"))
        ww = str(w.get("kijkwachtwoord", ""))
        poort = int(_getal(w, "poort", 1024, 65535, "Poort"))
        bind = _keuze(w, "bind", ("0.0.0.0", "127.0.0.1"), "Bereikbaar vanaf")
        if bev:
            if ww:
                if len(ww) < 8:
                    raise Fout("Het wachtwoord om te bekijken moet minstens 8 tekens hebben.")
                ec.zet_env(envpad, "HEMS_DASHBOARD_WACHTWOORD", ec.hash_wachtwoord(ww))
            elif not dashboard_beveiligd(cfg, cfgpad):
                raise Fout("Kies een wachtwoord om het dashboard te bekijken.")
        else:
            if env.get("HEMS_DASHBOARD_WACHTWOORD"):
                ec.zet_env(envpad, "HEMS_DASHBOARD_WACHTWOORD", "")
        oud_poort = ec.cfg_int(cfg, "dashboard", "poort", 8080)
        oud_bind = ec.cfg_str(cfg, "dashboard", "bind", "0.0.0.0")
        wijz = {("dashboard", "poort"): str(poort), ("dashboard", "bind"): bind}
        if ec.cfg_str(cfg, "dashboard", "wachtwoord"):
            wijz[("dashboard", "wachtwoord")] = ""      # oud wachtwoord in klare tekst: vervangen door de hash in .env
            if bev and not ww and not env.get("HEMS_DASHBOARD_WACHTWOORD"):
                ec.zet_env(envpad, "HEMS_DASHBOARD_WACHTWOORD", ec.hash_wachtwoord(ec.cfg_str(cfg, "dashboard", "wachtwoord")))
        ec.zet_config(cfgpad, wijz)
        if poort != oud_poort or bind != oud_bind:
            extra["herstart"] = True
            extra["poort"] = poort
            bericht += " Het dashboard herstart nu."
        if bev:
            bericht += " Je browser vraagt straks het wachtwoord om te bekijken."

    elif sectie == "algemeen":
        tz = str(w.get("tijdzone", "")).strip()
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)
        except Exception:
            raise Fout("Onbekende tijdzone.")
        if "/" not in tz or any(c.isspace() for c in tz):
            raise Fout("Onbekende tijdzone.")
        ec.zet_config(cfgpad, {("algemeen", "tijdzone"): tz})

    else:
        raise Fout("Onbekend onderdeel.")

    print(f"instellingen: onderdeel '{sectie}' opgeslagen via het dashboard", flush=True)
    return {"ok": True, "bericht": bericht, **extra}


# ---------- hulpknoppen ----------

def maandindex_lijst(cfg, db_pad, maanden=4):
    """De laatste maanden met schatting en officiële waarde, voor de tabel op de pagina."""
    import ems_kosten
    tz = ec.tijdzone(cfg)
    nu = ec.utc_nu().astimezone(tz)
    j, m = nu.year, nu.month
    uit = []
    db = ec.open_db(db_pad)
    try:
        for _ in range(maanden):
            sleutel = f"{j:04d}-{m:02d}"
            rij = {"maand": sleutel}
            for soort in ("afname", "injectie"):
                off = db.execute("SELECT eur_mwh FROM maandindex WHERE maand=? AND soort=?", (sleutel, soort)).fetchone()
                rij[soort] = _getal_tekst(off[0]) if off and off[0] is not None else ""
                sch = ems_kosten.maandindex(db, j, m, soort, tz, officieel=False)[0]
                rij[soort + "_schatting"] = _getal_tekst(sch) if sch is not None else "–"
            uit.append(rij)
            j, m = (j - 1, 12) if m == 1 else (j, m - 1)
    finally:
        db.close()
    return uit


def maandindex_opslaan(data, db_pad):
    import ems_kosten
    maand = str(data.get("maand", ""))
    if not re.match(r"^20\d\d-(0[1-9]|1[0-2])$", maand):
        return {"ok": False, "fout": "Ongeldige maand."}
    db = ec.open_db(db_pad)
    try:
        for soort in ("afname", "injectie"):
            rauw = str(data.get(soort, "")).strip().replace(",", ".")
            if rauw == "":
                ems_kosten.zet_maandindex(db, maand, soort, None)
                continue
            try:
                x = float(rauw)
            except ValueError:
                return {"ok": False, "fout": f"De index {soort} moet een getal zijn (in €/MWh)."}
            if not -500 <= x <= 2000:
                return {"ok": False, "fout": "Een maandindex ligt normaal tussen -500 en 2000 €/MWh."}
            ems_kosten.zet_maandindex(db, maand, soort, x)
    finally:
        db.close()
    return {"ok": True, "bericht": f"Maandindex {maand} opgeslagen. De kosten van die maand worden ermee herrekend."}


def prijstest(cfg, cfgpad):
    import ems_prijzen
    tz = ec.tijdzone(cfg)
    try:
        prijzen, bron = ems_prijzen.haal(cfg, cfgpad, 2)
    except urllib.error.URLError as e:
        return {"ok": False, "fout": f"Geen verbinding met de bron (internet?): {getattr(e, 'reason', e)}"}
    except Exception as e:
        return {"ok": False, "fout": f"Ophalen mislukt: {e}"}
    if not prijzen:
        return {"ok": False, "fout": "De bron gaf geen prijzen terug."}
    ems_prijzen.bewaar(cfgpad, cfg, prijzen, bron)
    nu = ec.utc_nu()
    prijzen.sort()
    huidig = None
    for i, (start, p) in enumerate(prijzen):
        volgende = prijzen[i + 1][0] if i + 1 < len(prijzen) else start + timedelta(hours=1)
        if start <= nu < volgende:
            huidig = p
    vandaag = nu.astimezone(tz).date()
    van_vandaag = [p for s, p in prijzen if s.astimezone(tz).date() == vandaag]
    laatste = prijzen[-1][0].astimezone(tz)
    tekst = f"✓ {len(prijzen)} prijzen opgehaald en bewaard, tot {laatste.strftime('%d/%m %H:%M')}."
    if huidig is not None:
        soort = "kale marktprijs, zonder kosten en btw" if bron == "entsoe" else "marktprijs met btw, zonder kosten van de leverancier"
        tekst += f" Nu: {ec.komma(huidig * 100, 2)} c€/kWh ({soort})."
    if van_vandaag:
        tekst += f" Vandaag van {ec.komma(min(van_vandaag) * 100, 2)} tot {ec.komma(max(van_vandaag) * 100, 2)} c€/kWh."
    if laatste.date() <= vandaag:
        tekst += " De prijzen van morgen komen rond 13 uur; daarna worden ze automatisch opgehaald."
    return {"ok": True, "bericht": tekst}


def mailtest(cfg, cfgpad):
    if not ec.cfg_bool(cfg, "mail", "aan", False):
        return {"ok": False, "fout": "Zet eerst 'Dagrapport elke ochtend mailen' aan en sla op."}
    naar = ec.cfg_str(cfg, "mail", "naar") or ec.cfg_str(cfg, "mail", "gebruiker")
    try:
        ec.stuur_mail(cfg, cfgpad, "HEMS: testmail",
                      "Deze testmail komt van je HEMS-meetsysteem.\n\nLees je dit, dan werkt de mail en krijg je "
                      "voortaan elke ochtend het dagrapport.")
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "fout": "De mailserver weigert het wachtwoord. Bij Gmail: gebruik een app-wachtwoord, "
                                     "niet je gewone wachtwoord."}
    except Exception as e:
        return {"ok": False, "fout": f"Versturen mislukt: {e}"}
    return {"ok": True, "bericht": f"✓ Testmail verstuurd naar {naar}. Niets ontvangen? Kijk ook bij spam."}


# ---------- de pagina ----------

STIJL_KIOSK = """
body.kiosk{max-width:1180px;font-size:17px}
.kiosk h1{font-size:1.6em}
.indeling{display:grid;grid-template-columns:270px 1fr;gap:18px;align-items:start}
.zij{position:sticky;top:10px;display:flex;flex-direction:column;gap:6px}
.zij button{display:flex;align-items:center;gap:10px;width:100%;text-align:left;margin:0;padding:12px 14px;
 font-size:1em;background:#fff;border:1px solid #ddd;border-radius:10px}
.zij button.actief{border-color:#0072B2;box-shadow:inset 4px 0 0 #0072B2;background:#eef6fb}
.zij .ic{font-size:1.25em;width:1.5em;text-align:center}
.zij .nm{flex:1}
.stip{width:12px;height:12px;border-radius:50%;flex:none}
.stip.ok{background:#009E73}.stip.let{background:#E69F00}.stip.opt{background:#bbb}
.inhoud .paneel{padding:18px 22px}
.inhoud h2{font-size:1.35em;margin:.1em 0 .4em}
.intro{color:#333;margin:0 0 .8em}
.veld{margin:14px 0}
.veld label{font-size:1em}
.veld .hulp{font-size:.88em;color:#555;margin-top:4px;max-width:620px}
.kiosk input[type=text],.kiosk input[type=password],.kiosk select{font-size:1.05em;padding:10px;max-width:620px}
.metenheid{display:flex;align-items:center;gap:8px;max-width:620px}
.metenheid input{max-width:200px!important}
.vink{display:flex;align-items:center;gap:12px;font-weight:bold;cursor:pointer}
.vink input{width:26px;height:26px}
.knoppen{margin-top:18px;display:flex;flex-wrap:wrap;gap:8px}
.kiosk button{font-size:1.02em;padding:10px 18px;margin:0}
.kaartlijst{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.okaart{background:#fff;border:1px solid #ddd;border-radius:10px;padding:12px 14px;cursor:pointer;display:flex;gap:10px;align-items:flex-start}
.okaart:hover{border-color:#0072B2}
.okaart .t{font-weight:bold}.okaart .s{font-size:.9em;color:#444}
.welkom{background:#eef6fb;border:1px solid #0072B2;border-radius:10px;padding:12px 16px;margin-bottom:12px}
.gewijzigd{color:#B35900;font-size:.9em;margin-left:6px}
.kiosk a.knop{display:inline-block;padding:10px 18px;border:1px solid #888;border-radius:6px;text-decoration:none;color:#222;background:#f4f4f4}
.kiosk a.knop.hoofd{background:#0072B2;color:#fff;border-color:#0072B2}
.kiosk pre.md{white-space:pre-wrap;background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;font-size:.85em;max-height:520px;overflow:auto}
.kop h3{margin:22px 0 2px;font-size:1.1em;border-bottom:1px solid #ddd;padding-bottom:4px}
.inhoud table{font-size:.95em}.inhoud td input{padding:6px!important}
@media(max-width:760px){.indeling{grid-template-columns:1fr}
 .zij{position:static;flex-direction:row;overflow-x:auto;padding-bottom:6px}
 .zij button{width:auto;flex:none;padding:10px 12px}.zij .nm{white-space:nowrap}}
"""

_JS = r"""
const D=INIT;const CSRF=D.csrf;
const S=Object.fromEntries(D.secties.map(s=>[s.id,s]));
let actief=null,gewijzigd={},GEVONDEN=[];
const $=id=>document.getElementById(id);
function maak(tag,attr,...kind){const e=document.createElement(tag);for(const[k,v]of Object.entries(attr||{})){
 if(k==='html')e.innerHTML=v;else if(k.startsWith('on'))e[k]=v;else if(v!==null&&v!==undefined&&v!==false)e.setAttribute(k,v===true?'':v)}
 for(const k of kind){if(k!=null)e.append(k)}return e}
async function post(pad,data){
 const r=await fetch(pad,{method:'POST',headers:{'Content-Type':'application/json','X-HEMS-CSRF':CSRF},body:JSON.stringify(data)});
 if(r.status===401){alert('Je bent afgemeld (sessie verlopen). Meld opnieuw aan.');location.reload();throw new Error('afgemeld')}
 return r.json()}
function meld(id,ok,tekst){const e=$(id);if(!e)return;e.className='melding '+(ok?'ok':'fout');e.textContent=tekst}
function stip(id){const o=D.overzicht.find(x=>x.id===id);return o?o.staat:''}
function nav(){const z=$('zij');z.innerHTML='';
 for(const s of D.secties){const st=stip(s.id);
  z.append(maak('button',{type:'button',class:s.id===actief?'actief':'',onclick:()=>ga(s.id)},
   maak('span',{class:'ic'},s.icoon),maak('span',{class:'nm'},s.titel+(gewijzigd[s.id]?' •':'')),
   st?maak('span',{class:'stip '+st,title:st==='ok'?'in orde':st==='let'?'vraagt aandacht':'optioneel'}):null))}
 z.append(maak('button',{type:'button',onclick:afmelden},maak('span',{class:'ic'},'⏻'),maak('span',{class:'nm'},'Afmelden')))}
function ga(id){if(actief&&gewijzigd[actief]&&id!==actief&&!confirm('Je wijzigingen in "'+S[actief].titel+'" zijn nog niet opgeslagen. Toch verdergaan?'))return;
 if(actief&&id!==actief)delete gewijzigd[actief];
 actief=id;history.replaceState(null,'','#'+id);nav();teken();window.scrollTo(0,0)}
function volgende(id){const i=D.secties.findIndex(s=>s.id===id);return D.secties[i+1]?D.secties[i+1].id:(S.overzicht?'overzicht':D.secties[0].id)}
function teken(){const c=$('inhoud');c.innerHTML='';
 if(window.EXTRA&&EXTRA[actief])return EXTRA[actief](c);
 if(actief==='overzicht')return tekenOverzicht(c);
 if(actief==='wachtwoord')return tekenWachtwoord(c);
 const s=S[actief],w=D.waarden[actief];
 const p=maak('div',{class:'paneel'},maak('h2',{},s.icoon+' '+s.titel),s.intro?maak('p',{class:'intro',html:s.intro}):null);
 if(actief==='meter')p.append(meterZoeker());
 for(const v of s.velden)p.append(veld(s,v,w));
 const kn=maak('div',{class:'knoppen'},
  maak('button',{type:'button',class:'hoofd',id:'bewaar',onclick:()=>bewaar(false)},'Opslaan'),
  maak('button',{type:'button',onclick:()=>bewaar(true)},'Opslaan en verder →'));
 if(actief==='tarief')p.append(maandIndexBlok());
 if(actief==='meter')kn.append(maak('button',{type:'button',id:'testknop',onclick:testMeter},'Test de meter'));
 if(actief==='prijzen')kn.append(maak('button',{type:'button',id:'prijsknop',onclick:testPrijzen},'Test en haal de prijzen op'));
 if(actief==='mail'&&D.waarden.mail.aan)kn.append(maak('button',{type:'button',id:'mailknop',onclick:testMail},'Testmail sturen'));
 if(actief==='installatie'&&D.fasen_meter)kn.append(maak('button',{type:'button',onclick:()=>{zet('fasen',String(D.fasen_meter))}},'Neem over uit de meter ('+D.fasen_meter+' fase'+(D.fasen_meter>1?'n':'')+')'));
 p.append(kn,maak('div',{class:'melding',id:'uit'}),maak('div',{class:'melding',id:'uit2'}));
 c.append(p);zichtbaar()}
function veld(s,v,w){const id='v_'+v.id;let inp;const val=w[v.id];
 if(v.type==='info')return maak('div',{class:'veld info',id:'r_'+v.id},maak('div',{class:'melding ok',style:'display:block'},v.label));
 if(v.type==='kop')return maak('div',{class:'veld kop',id:'r_'+v.id},maak('h3',{},v.label),v.hulp?maak('div',{class:'hulp'},v.hulp):null,
  v.id==='k_net'?maak('div',{class:'knoppen',style:'margin-top:8px'},maak('button',{type:'button',onclick:voorbeeldNet},'Vul voorbeeldwaarden heffingen in')):null);
 const wijzig=()=>{w[v.id]=lees(v);gewijzigd[actief]=true;nav();zichtbaar();if(v.id==='aanbieder')aanbieder();
  if(v.id==='gemeente'&&w.gemeente&&D.gemeenten[w.gemeente])zet('netgebied',D.gemeenten[w.gemeente]);
  if(v.id==='netgebied'&&w.netgebied)vulNetgebied(w.netgebied);
  if(['land','gewest','gemeente','netgebied','bedragen'].includes(v.id))vulHeffingen(v.id==='bedragen')};
 if(v.type==='keuze'){inp=maak('select',{id});for(const[o,t]of v.opties)inp.add(new Option(t,o));inp.value=String(val);inp.onchange=wijzig}
 else if(v.type==='vink'){inp=maak('input',{type:'checkbox',id});inp.checked=!!val;inp.onchange=wijzig;
  return maak('div',{class:'veld',id:'r_'+v.id},maak('label',{class:'vink',for:id},inp,v.label),v.hulp?maak('div',{class:'hulp'},v.hulp):null)}
 else if(v.type==='geheim'){inp=maak('input',{type:'password',id,autocomplete:'new-password',
  placeholder:val?'✓ ingesteld — leeg laten om te behouden':'nog niet ingesteld'});inp.oninput=wijzig}
 else{inp=maak('input',{type:'text',id,value:val??'',placeholder:v.plaats||'',autocomplete:'off',spellcheck:'false',
  inputmode:v.type==='getal'?'decimal':null});inp.oninput=wijzig}
 const invoer=v.eenheid?maak('div',{class:'metenheid'},inp,maak('span',{},v.eenheid)):inp;
 return maak('div',{class:'veld',id:'r_'+v.id},maak('label',{for:id},v.label),invoer,v.hulp?maak('div',{class:'hulp'},v.hulp):null)}
function lees(v){const e=$('v_'+v.id);if(!e)return null;return v.type==='vink'?e.checked:e.value}
function zet(id,waarde){const e=$('v_'+id);if(!e)return;e.value=waarde;e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input'))}
function zichtbaar(){const s=S[actief];if(!s||!s.velden)return;const w=D.waarden[actief];
 for(const v of s.velden){if(!v.toon)continue;const[a,b]=v.toon;const r=$('r_'+v.id);
  const een=(a,b)=>Array.isArray(b)?b.includes(String(w[a])):(typeof b==='boolean')?(!!w[a]===b):(String(w[a])===b);
  const ok=Array.isArray(v.toon[0])?v.toon.every(([x,y])=>een(x,y)):een(a,b);
  if(r)r.style.display=ok?'':'none'}
 if(actief==='tarief'){const mi=$('mi_blok');if(mi)mi.style.display=(['variabel','blok'].includes(w.soort)||w.inj_soort==='formule')?'':'none'}}
function vulNetgebied(k){const n=D.netgebieden[k];if(!n)return;
 const f=($('v_bedragen').value==='incl')?(1+Number(String($('v_btw').value||'6').replace(',','.'))/100):1;
 const t=(x,d)=>komma(x*f,d).replace(/0+$/,'').replace(/,$/,'');
 zet('distributie',t(n.distributie_eur_kwh*100,5));zet('capaciteit',t(n.capaciteit_eur_kw_jaar,4));zet('databeheer',t(n.databeheer_eur_jaar,2));
 meld('uit',true,'Netkosten van '+n.naam+' ingevuld (officiële tarieven '+D.nettarief_geldig[0].slice(0,4)+(f>1?', omgerekend naar incl. btw':'')+'): distributie '+t(n.distributie_eur_kwh*100,5)+' c€/kWh, capaciteitstarief '+t(n.capaciteit_eur_kw_jaar,2)+' €/kW/jaar, databeheer '+t(n.databeheer_eur_jaar,2)+' €/jaar. Klik op Opslaan.')}
function vulHeffingen(alles){const w=D.waarden.tarief;if(w.land!=='BE')return;
 const f=($('v_bedragen').value==='incl')?(1+Number(String($('v_btw').value||'6').replace(',','.'))/100):1;
 const t=x=>komma(x*100*f,5).replace(/0+$/,'').replace(/,$/,'');const leeg=id=>!String(($('v_'+id)||{}).value||'').trim();
 const h=D.heffingen_be,gedaan=[];
 // federaal: altijd invullen (wettelijk vast); certificaten: enkel als leeg (of bij omschakelen excl/incl)
 zet('energiebijdrage',t(h.energiebijdrage_eur_kwh));zet('accijns',t(h.accijns_eur_kwh));gedaan.push('energiebijdrage en accijns (federaal)');
 if(w.gewest==='VL'){const c=D.certificaten_vl;
  if(alles||leeg('groenestroom')){zet('groenestroom',t(c.groenestroom_eur_kwh));gedaan.push('groenestroom')}
  if(alles||leeg('wkk')){zet('wkk',t(c.wkk_eur_kwh));gedaan.push('WKK (gangbare waarde, controleer op je tariefkaart)')}}
 meld('uit2',true,'Ingevuld: '+gedaan.join(', ')+(f>1?' — incl. btw':'')+'. Klik op Opslaan.')}
function voorbeeldNet(){const v=D.voorbeeld_net,w=D.waarden.tarief;
 const f=($('v_bedragen').value==='incl')?(1+v.btw/100):1;
 const z=(id,x,d)=>zet(id,komma(x*f,d).replace(/,?0+$/,'').replace(/,$/,''));
 z('groenestroom',v.groenestroom,5);z('wkk',v.wkk,5);z('energiebijdrage',v.energiebijdrage,5);z('accijns',v.accijns,5);zet('btw',String(v.btw));
 meld('uit',true,'Voorbeeldwaarden voor de heffingen ingevuld (zoals op een gangbare tariefkaart 2026'+(f>1?', omgerekend naar incl. btw':'')+'). Groenestroom en WKK verschillen per leverancier: vergelijk met je eigen factuur en klik dan op Opslaan.')}
function maandIndexBlok(){
 const t=maak('table',{},maak('tr',{},maak('th',{},'Maand'),maak('th',{},'Afname: schatting'),maak('th',{},'Afname: officieel'),
  maak('th',{},'Injectie: schatting'),maak('th',{},'Injectie: officieel'),maak('th',{},'')));
 for(const r of D.maandindex){const a=maak('input',{type:'text',value:r.afname,inputmode:'decimal',placeholder:'€/MWh',style:'max-width:110px'}),
  i=maak('input',{type:'text',value:r.injectie,inputmode:'decimal',placeholder:'€/MWh',style:'max-width:110px'});
  const m=r.maand.split('-');
  t.append(maak('tr',{},maak('td',{},m[1]+'/'+m[0]),maak('td',{},r.afname_schatting),maak('td',{},a),maak('td',{},r.injectie_schatting),maak('td',{},i),
   maak('td',{},maak('button',{type:'button',onclick:async()=>{const j=await post('/instellingen/maandindex',{maand:r.maand,afname:a.value,injectie:i.value});meld('mi_uit',j.ok,j.ok?j.bericht:j.fout)}},'Bewaar'))))}
 return maak('div',{class:'veld',id:'mi_blok'},maak('h3',{},'Maandindex (voor formules met een maandindex)'),
  maak('div',{class:'hulp',html:'De leverancier publiceert de index (bv. Belpex-RLP-M voor afname en Belpex-SPP-M voor injectie) na het einde van de maand, in €/MWh; ze staat ook op je factuur. Tot dan rekent het systeem met een <b>schatting</b> uit de marktprijzen, gewogen naar je eigen afname en teruglevering. Vul de officiële waarde in zodra je ze hebt: de kosten van die maand worden dan juist.'}),
  t,maak('div',{class:'melding',id:'mi_uit'}))}
function aanbieder(){if($('v_aanbieder').value==='gmail'){zet('smtp_host','smtp.gmail.com');zet('smtp_poort','465')}}
async function bewaar(verder){const s=S[actief],w={};
 for(const v of s.velden)if(v.type!=='kop')w[v.id]=lees(v);
 const k=$('bewaar');k.disabled=true;
 try{const j=await post(D.opslaan_pad||'/instellingen/opslaan',{sectie:actief,waarden:w});
  if(!j.ok){meld('uit',false,j.fout);return}
  D.overzicht=j.overzicht;D.waarden=j.waarden;D.fasen_meter=j.fasen_meter;delete gewijzigd[actief];
  if(j.herstart){meld('uit',true,j.bericht+' Even geduld…');
   const doel=location.protocol+'//'+location.hostname+':'+j.poort+'/instellingen#dashboard';
   setTimeout(()=>{location.href=doel},8000);return}
  if(verder){const vlg=volgende(actief);ga(vlg);meld('uit',true,j.bericht)}else{nav();teken();meld('uit',true,j.bericht)}
 }catch(e){meld('uit',false,'Opslaan mislukt: '+e)}finally{const b=$('bewaar');if(b)b.disabled=false}}
function komma(x,d){return x==null?'-':Number(x).toFixed(d).replace('.',',')}
function wtekst(x){if(x==null)return '-';const a=Math.abs(x);return (a>=1000?komma(a/1000,2)+' kW':komma(a,0)+' W')+(x<0?' teruglevering':' afname')}
function meterZoeker(){return maak('div',{class:'veld'},
 maak('button',{type:'button',class:'hoofd',id:'zoekknop',onclick:zoek},'🔎 Zoek mijn meter'),
 maak('span',{id:'zoekinfo',class:'uitleg'}),
 maak('div',{id:'lijstblok',style:'display:none'},maak('label',{for:'lijst'},'Gevonden toestellen'),
  maak('select',{id:'lijst',onchange:kies})))}
async function zoek(){const k=$('zoekknop'),info=$('zoekinfo');k.disabled=true;info.textContent=' Zoeken op je netwerk… (ca. 5 tot 10 s)';
 try{const j=await post('/instellingen/zoek',{});GEVONDEN=j.toestellen||[];const l=$('lijst');l.innerHTML='';
  if(!GEVONDEN.length){info.textContent=' Geen HomeWizard-toestel gevonden op '+(j.netten||[]).join(', ')+'. Staat de meter aan en op hetzelfde netwerk? Je kan het adres ook zelf invullen.';$('lijstblok').style.display='none';return}
  l.add(new Option('— kies een toestel —',''));
  GEVONDEN.forEach((t,i)=>{let s=t.naam+' – '+t.ip;if(t.api_v1===true&&t.vermogen_w!=null)s+=' – nu '+wtekst(t.vermogen_w);
   if(t.api_v1===false)s+=' – lokale API staat UIT';if(!t.p1)s+=' (geen P1-meter)';l.add(new Option(s,String(i)))});
  $('lijstblok').style.display='block';info.textContent=' '+GEVONDEN.length+' gevonden.';
  const p1=GEVONDEN.filter(t=>t.p1);if(p1.length===1){l.value=String(GEVONDEN.indexOf(p1[0]));kies()}
 }catch(e){info.textContent=' Zoeken mislukt: '+e}finally{k.disabled=false}}
function kies(){const v=$('lijst').value;if(v==='')return;const t=GEVONDEN[+v];zet('url',t.url);
 if(t.api_v1===false)meld('uit2',false,'Dit toestel weigert: zet de lokale API aan in de HomeWizard-app (Instellingen → Meters → je P1 → Lokale API) en klik dan op "Test de meter".');
 else testMeter()}
async function testMeter(){const k=$('testknop');k.disabled=true;meld('uit2',true,'Testen…');
 try{const j=await post('/instellingen/test',{url:$('v_url').value.trim()});
  if(!j.ok){meld('uit2',false,j.fout);return}const jn=b=>b?'ja':'nee';
  meld('uit2',true,'✓ De meter antwoordt: nu '+wtekst(j.vermogen_w)+'. Per fase: '+jn(j.fasen)+' · stroom: '+jn(j.stroom)+' · spanning: '+jn(j.spanning)+' · kwartiergemiddelde: '+jn(j.kwartier)+'. Klik nu op Opslaan.')
 }catch(e){meld('uit2',false,'Test mislukt: '+e)}finally{k.disabled=false}}
async function testPrijzen(){if(gewijzigd.prijzen){meld('uit2',false,'Sla eerst op, dan testen.');return}
 const k=$('prijsknop');k.disabled=true;meld('uit2',true,'Prijzen ophalen… (kan tot 30 s duren)');
 try{const j=await post('/instellingen/prijstest',{});meld('uit2',j.ok,j.ok?j.bericht:j.fout);if(j.overzicht){D.overzicht=j.overzicht;nav()}}
 catch(e){meld('uit2',false,'Mislukt: '+e)}finally{k.disabled=false}}
async function testMail(){if(gewijzigd.mail){meld('uit2',false,'Sla eerst op, dan testen.');return}
 const k=$('mailknop');k.disabled=true;meld('uit2',true,'Testmail versturen…');
 try{const j=await post('/instellingen/mailtest',{});meld('uit2',j.ok,j.ok?j.bericht:j.fout)}
 catch(e){meld('uit2',false,'Mislukt: '+e)}finally{k.disabled=false}}
function tekenOverzicht(c){const te=D.overzicht.filter(o=>o.staat==='let');
 const p=maak('div',{class:'paneel'},maak('h2',{},'🏠 Overzicht'));
 if(te.length)p.append(maak('div',{class:'welkom',html:'<b>Nog '+te.length+' onderdeel'+(te.length>1?'en':'')+' te doen.</b> Klik op een oranje kaart, of begin bij <b>'+S[te[0].id].titel+'</b>.'}));
 else p.append(maak('div',{class:'welkom',html:'<b>Alles is ingesteld.</b> De grijze onderdelen zijn optioneel.'}));
 const l=maak('div',{class:'kaartlijst'});
 for(const o of D.overzicht){const s=S[o.id];
  l.append(maak('div',{class:'okaart',onclick:()=>ga(o.id)},maak('span',{class:'stip '+o.staat,style:'margin-top:6px'}),
   maak('div',{},maak('div',{class:'t'},s.icoon+' '+s.titel),maak('div',{class:'s'},o.tekst))))}
 p.append(l,maak('p',{class:'uitleg'},'Groen = in orde · oranje = vraagt aandacht · grijs = optioneel. Wijzigingen gaan binnen ca. 10 seconden in; herstarten is niet nodig.'));
 c.append(p);
 if(te.length)c.append(maak('div',{class:'knoppen'},maak('button',{type:'button',class:'hoofd',onclick:()=>ga(te[0].id)},'Begin bij '+S[te[0].id].titel+' →')))}
function tekenWachtwoord(c){const p=maak('div',{class:'paneel'},maak('h2',{},'🔑 Beheerwachtwoord'),
 maak('p',{class:'intro'},'Met dit wachtwoord open je deze pagina Instellingen.'));
 for(const[id,lbl,ac]of[['oud','Huidig wachtwoord','current-password'],['n1','Nieuw wachtwoord (minstens 8 tekens)','new-password'],['n2','Nieuw wachtwoord, nog eens','new-password']])
  p.append(maak('div',{class:'veld'},maak('label',{for:id},lbl),maak('input',{type:'password',id,autocomplete:ac})));
 p.append(maak('div',{class:'knoppen'},maak('button',{type:'button',class:'hoofd',onclick:async()=>{
  const j=await post('/instellingen/wachtwoord',{oud:$('oud').value,n1:$('n1').value,n2:$('n2').value});
  meld('uit',j.ok,j.ok?'✓ Wachtwoord gewijzigd.':j.fout);if(j.ok)['oud','n1','n2'].forEach(i=>$(i).value='')}},'Wachtwoord wijzigen')),
  maak('div',{class:'melding',id:'uit'}));c.append(p)}
async function afmelden(){await fetch('/instellingen/afmelden',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'csrf='+encodeURIComponent(CSRF)});location.href='/'}
window.addEventListener('beforeunload',e=>{if(Object.keys(gewijzigd).length){e.preventDefault();e.returnValue=''}});
if(!window.EXTRA)window.EXTRA={};
if(S.ai&&!EXTRA.ai)EXTRA.ai=c=>{c.append(maak('div',{class:'paneel'},maak('h2',{},'🤖 Later sturing met AI'),
 maak('p',{class:'intro',html:'Dit systeem <b>meet</b> en stuurt niets. Wil je later ook je batterij, laadpaal of warmtepomp laten sturen, '+
  'dan kan een AI-assistent dat bouwen met het <a href="https://github.com/luc-vancraen/home-energy-management-system-template" target="_blank" rel="noopener">HEMS-template</a>. '+
  'Vul eerst de vragenlijst in: wat je antwoordt, komt in het bestand <b>mijn-installatie.md</b> dat je aan de assistent geeft. '+
  'Dan hoeft die enkel nog te vragen wat ontbreekt.'}),
 maak('div',{class:'knoppen'},maak('a',{href:'/instellingen/vragenlijst',class:'knop hoofd'},'Vragenlijst "Mijn installatie" openen →'))))};
const start=location.hash.slice(1);actief=S[start]?start:(S.overzicht?'overzicht':D.secties[0].id);nav();teken();
"""


def pagina(csrf, cfg, cfgpad, db_pad, stijl_basis):
    data = {
        "csrf": csrf,
        "secties": schema(cfg),
        "waarden": waarden(cfg, cfgpad),
        "overzicht": overzicht(cfg, cfgpad, db_pad),
        "fasen_meter": fasen_uit_meter(db_pad) if not ec.demo_actief(cfg) else None,
        "maandindex": maandindex_lijst(cfg, db_pad),
        "voorbeeld_net": __import__("ems_kosten").VOORBEELD_NET,
        "netgebieden": _ng().NETGEBIEDEN, "gemeenten": _ng().GEMEENTEN, "nettarief_geldig": [_ng().GELDIG_VAN, _ng().GELDIG_TOT],
        "heffingen_be": _ng().HEFFINGEN_BE, "certificaten_vl": _ng().CERTIFICATEN_VL,
    }
    # </ in JSON onschadelijk maken zodat niets het script-blok kan afsluiten
    init = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return ("<!doctype html><html lang='nl'><head><meta charset='utf-8'><title>HEMS — instellingen</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{stijl_basis}{STIJL_KIOSK}</style></head><body class='kiosk'>"
            "<h1>HEMS — instellingen</h1><p class='nav'><a href='/'>&larr; naar het dashboard</a></p>"
            "<noscript><p class='melding fout'>Deze pagina heeft JavaScript nodig.</p></noscript>"
            "<div class='indeling'><nav class='zij' id='zij'></nav><main class='inhoud' id='inhoud'></main></div>"
            "<p class='voet'>Instellingen wijzigen alleen de eigen configuratie van dit meetsysteem (met back-up), "
            "nooit een toestel. Copyright (c) 2026 luc-vancraen</p>"
            f"<script>const INIT={init};{_JS}</script></body></html>")


def ontsnap(x):
    return html.escape(str(x))
