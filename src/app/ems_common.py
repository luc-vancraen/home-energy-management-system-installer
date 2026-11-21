"""Gedeelde hulpcode voor het HEMS-meetsysteem.

Het systeem meet en rapporteert alleen; het schrijft nooit naar een toestel.
Alle tijden worden intern in UTC opgeslagen en pas bij weergave omgezet naar
de lokale tijd (tijdzone uit de configuratie).
"""
import configparser
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:          # Python < 3.9 heeft geen zoneinfo
    ZoneInfo = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS metingen (
  ts TEXT PRIMARY KEY,        -- UTC, ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)
  import_kwh REAL,            -- teller afname (t1+t2)
  export_kwh REAL,            -- teller injectie (t1+t2)
  vermogen_w REAL,            -- ogenblikkelijk vermogen (+ afname / - injectie)
  l1_w REAL, l2_w REAL, l3_w REAL,
  l1_a REAL, l2_a REAL, l3_a REAL,
  l1_v REAL, l2_v REAL, l3_v REAL,
  gas_m3 REAL, water_m3 REAL,
  meter_kwartier_w REAL,      -- lopend kwartiergemiddelde zoals de meter het geeft (indien aanwezig)
  meter_maandpiek_w REAL      -- maandpiek zoals de meter die geeft (indien aanwezig)
);
CREATE TABLE IF NOT EXISTS kwartieren (
  einde_utc TEXT PRIMARY KEY, -- UTC-tijdstip waarop het kwartier eindigt
  import_kwh REAL,
  export_kwh REAL,
  gemiddeld_w REAL,           -- gemiddeld afnamevermogen over het kwartier
  compleet INTEGER            -- 1 = voldoende metingen, 0 = gaten in de data
);
CREATE TABLE IF NOT EXISTS maandindex (
  maand TEXT,                 -- 'JJJJ-MM'
  soort TEXT,                 -- 'afname' (bv. Belpex-RLP-M) of 'injectie' (bv. Belpex-SPP-M)
  eur_mwh REAL,               -- officiële waarde zoals de leverancier ze publiceert
  PRIMARY KEY (maand, soort)
);
CREATE TABLE IF NOT EXISTS prijzen (
  start_utc TEXT PRIMARY KEY, -- begin van het kwartier/uur (UTC)
  eur_kwh REAL,               -- kale marktprijs per kWh
  bron TEXT
);
"""

KLEUR_IMPORT = "#E69F00"      # oranje (kleurenblindveilig)
KLEUR_EXPORT = "#0072B2"      # blauw (kleurenblindveilig)
KLEUR_TEKST = "#222222"
KLEUR_RASTER = "#cccccc"


# ---------- configuratie ----------

def laad_config(pad):
    p = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    with open(pad, encoding="utf-8") as f:
        p.read_file(f)
    return p


def cfg_str(cfg, sectie, sleutel, standaard=""):
    return cfg.get(sectie, sleutel, fallback=standaard).strip()


def cfg_int(cfg, sectie, sleutel, standaard):
    try:
        return cfg.getint(sectie, sleutel, fallback=standaard)
    except ValueError:
        return standaard


def cfg_float(cfg, sectie, sleutel, standaard):
    try:
        return cfg.getfloat(sectie, sleutel, fallback=standaard)
    except ValueError:
        return standaard


def cfg_bool(cfg, sectie, sleutel, standaard):
    rauw = cfg_str(cfg, sectie, sleutel, "")
    if not rauw:
        return standaard
    return rauw.strip().lower() in ("1", "ja", "j", "yes", "y", "true", "on")


def projectroot(cfgpad):
    """De projectmap is de ouder van de map waarin het configuratiebestand staat."""
    return os.path.dirname(os.path.dirname(os.path.abspath(cfgpad)))


def pad_in_project(cfgpad, relatief):
    if os.path.isabs(relatief):
        return relatief
    return os.path.join(projectroot(cfgpad), relatief)


def demo_actief(cfg, forceer=False):
    """Demo-modus volgens [meter] demo: auto (standaard) = demo zolang url leeg is, ja = altijd, nee = nooit."""
    if forceer:
        return True
    stand = cfg_str(cfg, "meter", "demo", "auto").lower()
    if stand in ("1", "ja", "j", "yes", "y", "true", "on"):
        return True
    if stand in ("0", "nee", "n", "no", "false", "off"):
        return False
    return not cfg_str(cfg, "meter", "url")


def db_pad(cfgpad, cfg, forceer_demo=False):
    """Pad naar de databank. Demo-metingen gaan naar een aparte databank (ems-demo.db),
    zodat gesimuleerde data nooit tussen de echte metingen en maandpieken belandt."""
    pad = pad_in_project(cfgpad, cfg_str(cfg, "opslag", "db_pad", "data/ems.db"))
    if demo_actief(cfg, forceer_demo):
        basis, ext = os.path.splitext(pad)
        pad = basis + "-demo" + (ext or ".db")
    return pad


def lees_env(pad):
    """Lees een eenvoudig .env-bestand (SLEUTEL=waarde per regel)."""
    waarden = {}
    try:
        with open(pad, encoding="utf-8") as f:
            for regel in f:
                regel = regel.strip()
                if regel and not regel.startswith("#") and "=" in regel:
                    k, v = regel.split("=", 1)
                    waarden[k.strip()] = v.strip()
    except OSError:
        pass
    return waarden


# ---------- configuratie en geheimen schrijven (pagina Instellingen) ----------

_SLEUTEL_RE = re.compile(r"^[A-Za-z0-9_]+$")


def config_mtime(pad):
    """Wijzigingstijd van het configuratiebestand (om wijzigingen op te merken)."""
    try:
        return os.stat(pad).st_mtime_ns
    except OSError:
        return None


def _schrijf_atomair(pad, tekst, modus=None):
    """Schrijf via een tijdelijk bestand + rename, zodat een lezer nooit een half bestand ziet."""
    map_ = os.path.dirname(os.path.abspath(pad))
    tmp = os.path.join(map_, "." + os.path.basename(pad) + ".tmp")
    if modus is None:
        try:
            modus = os.stat(pad).st_mode & 0o777
        except OSError:
            modus = 0o644
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, modus)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tekst)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, modus)
    os.replace(tmp, pad)


def zet_config(pad, waarden, bewaar_backups=10):
    """Pas waarden in ems.ini aan zonder commentaar of volgorde te verliezen.

    waarden: {(sectie, sleutel): waarde}. Ontbrekende sleutels of secties worden
    toegevoegd. Er komt eerst een back-up (ems.ini.bak.JJJJMMDD-UUMMSS); de
    oudste back-ups boven 'bewaar_backups' worden opgeruimd.
    """
    for (sectie, sleutel), waarde in waarden.items():
        if not _SLEUTEL_RE.match(sectie) or not _SLEUTEL_RE.match(sleutel):
            raise ValueError(f"ongeldige sleutel: [{sectie}] {sleutel}")
        w = str(waarde)
        # geen regeleinde, geen % (interpolatie) en geen " #" of " ;" (zou als commentaar gelezen worden)
        if any(c in w for c in "\r\n%") or w != w.strip() or re.search(r"\s[#;]", w):
            raise ValueError(f"ongeldige waarde voor [{sectie}] {sleutel}")
    with open(pad, encoding="utf-8") as f:
        regels = f.read().splitlines()

    nog = {k: str(v) for k, v in waarden.items()}

    def regel(sleutel, waarde):
        return f"{sleutel} = {waarde}" if waarde else f"{sleutel} ="

    def voeg_rest_toe(uit, sectie):
        lege = []
        while uit and not uit[-1].strip():
            lege.append(uit.pop())
        for (s, k) in [x for x in nog if x[0] == sectie]:
            uit.append(regel(k, nog.pop((s, k))))
        uit.extend(lege)

    uit, sectie = [], None
    for r in regels:
        m = re.match(r"\s*\[([^\]]+)\]\s*$", r)
        if m:
            if sectie is not None:
                voeg_rest_toe(uit, sectie)
            sectie = m.group(1).strip()
            uit.append(r)
            continue
        m = re.match(r"\s*([A-Za-z0-9_]+)\s*=", r)
        if m and sectie is not None and (sectie, m.group(1)) in nog:
            uit.append(regel(m.group(1), nog.pop((sectie, m.group(1)))))
            continue
        uit.append(r)
    if sectie is not None:
        voeg_rest_toe(uit, sectie)
    for s in sorted({x[0] for x in nog}):
        uit.extend(["", f"[{s}]"])
        for (s2, k) in [x for x in nog if x[0] == s]:
            uit.append(regel(k, nog.pop((s2, k))))

    stempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(pad, f"{pad}.bak.{stempel}")
    _schrijf_atomair(pad, "\n".join(uit) + "\n")
    map_, naam = os.path.split(os.path.abspath(pad))
    oud = sorted(f for f in os.listdir(map_) if f.startswith(naam + ".bak."))
    for f in oud[:-bewaar_backups] if bewaar_backups else []:
        try:
            os.remove(os.path.join(map_, f))
        except OSError:
            pass


def zet_env(pad, sleutel, waarde):
    """Zet of vervang SLEUTEL=waarde in .env (alleen leesbaar voor de eigenaar)."""
    if not _SLEUTEL_RE.match(sleutel) or any(c in waarde for c in "\r\n"):
        raise ValueError("ongeldige .env-waarde")
    regels = []
    try:
        with open(pad, encoding="utf-8") as f:
            regels = [r.rstrip("\n") for r in f if not r.startswith(sleutel + "=")]
    except OSError:
        pass
    regels.append(f"{sleutel}={waarde}")
    _schrijf_atomair(pad, "\n".join(regels) + "\n", 0o600)


def hash_wachtwoord(wachtwoord, iteraties=200_000):
    zout = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", wachtwoord.encode("utf-8"), bytes.fromhex(zout), iteraties).hex()
    return f"pbkdf2_sha256:{iteraties}:{zout}:{h}"


def check_wachtwoord(wachtwoord, opgeslagen):
    try:
        soort, n, zout, h = opgeslagen.split(":")
        if soort != "pbkdf2_sha256":
            return False
        poging = hashlib.pbkdf2_hmac("sha256", wachtwoord.encode("utf-8"), bytes.fromhex(zout), int(n)).hex()
        return hmac.compare_digest(poging, h)
    except Exception:
        return False


def instelcode_pad(cfgpad):
    return os.path.join(os.path.dirname(os.path.abspath(cfgpad)), "instelcode")


def instelcode(cfgpad, maak=True):
    """Eenmalige code om bij het eerste gebruik een beheerwachtwoord te kiezen.

    Staat in config/instelcode (alleen leesbaar voor de eigenaar); de installer
    toont ze. Ze vervalt zodra er een beheerwachtwoord is.
    """
    pad = instelcode_pad(cfgpad)
    try:
        with open(pad, encoding="utf-8") as f:
            code = f.read().strip()
        if code:
            return code
    except OSError:
        pass
    if not maak:
        return ""
    tekens = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # geen 0/O, 1/I: makkelijk over te typen
    code = "".join(secrets.choice(tekens) for _ in range(4)) + "-" + "".join(secrets.choice(tekens) for _ in range(4))
    _schrijf_atomair(pad, code + "\n", 0o600)
    return code


# ---------- meter zoeken en testen (pagina Instellingen; alleen GET) ----------

_CGNAT = ipaddress.ip_network("100.64.0.0/10")      # o.a. Tailscale


def is_lokaal_adres(host):
    """Alleen adressen op het eigen netwerk (privé, loopback, link-local, Tailscale)."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET)
        ip = ipaddress.ip_address(infos[0][4][0])
    except Exception:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in _CGNAT


def lees_json(url, timeout=5):
    verzoek = urllib.request.Request(url, headers={"User-Agent": "hems-meting/1.0"})
    with urllib.request.urlopen(verzoek, timeout=timeout) as r:
        return json.loads(r.read(200_000).decode("utf-8"))


def test_meter(url):
    """Lees één meting van een meter-URL; geeft een dict voor de pagina Instellingen."""
    deel = urllib.parse.urlparse(url)
    if deel.scheme not in ("http", "https") or not deel.hostname:
        return {"ok": False, "fout": "Het adres moet beginnen met http:// of https://."}
    if not is_lokaal_adres(deel.hostname):
        return {"ok": False, "fout": "Alleen adressen op je eigen netwerk zijn toegelaten."}
    try:
        d = lees_json(url, timeout=5)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return {"ok": False, "fout": "De meter weigert (403): zet de lokale API v1 aan in de HomeWizard-app "
                                         "(Instellingen -> Meters -> je P1 -> Lokale API)."}
        return {"ok": False, "fout": f"De meter antwoordt met fout {e.code}. Klopt het adres (eindigt op /api/v1/data)?"}
    except Exception as e:
        return {"ok": False, "fout": f"Geen antwoord van {deel.hostname}: {getattr(e, 'reason', e)}"}
    if not isinstance(d, dict) or "active_power_w" not in d:
        return {"ok": False, "fout": "Er komt een antwoord, maar het lijkt niet op een P1-meter (geen active_power_w)."}
    def heeft(*k):
        return any(d.get(x) is not None for x in k)
    return {
        "ok": True,
        "vermogen_w": d.get("active_power_w"),
        "fasen": heeft("active_power_l2_w", "active_power_l3_w"),
        "stroom": heeft("active_current_l1_a"),
        "spanning": heeft("active_voltage_l1_v"),
        "kwartier": heeft("active_power_average_w"),
        "maandpiek": heeft("montly_power_peak_w", "monthly_power_peak_w"),
    }


def eigen_netten():
    """De /24-netten waarin deze machine een privé-IPv4-adres heeft (via de standaardroute)."""
    ips = set()
    for doel in ("192.0.2.1", "10.255.255.255", "172.31.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((doel, 9))            # UDP: er wordt niets verstuurd
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    netten = []
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if a.is_private and not a.is_loopback:
            n = ipaddress.ip_network(ip + "/24", strict=False)
            if n not in netten:
                netten.append(n)
    return netten


def _poort_open(ip, poort=80, timeout=0.4):
    try:
        with socket.create_connection((ip, poort), timeout=timeout):
            return True
    except OSError:
        return False


def _homewizard_info(ip):
    try:
        d = lees_json(f"http://{ip}/api", timeout=2)
    except Exception:
        return None
    if not isinstance(d, dict) or "product_type" not in d:
        return None
    soort = str(d.get("product_type"))
    info = {"ip": ip, "type": soort, "naam": str(d.get("product_name") or soort),
            "url": f"http://{ip}/api/v1/data", "p1": soort.upper().startswith("HWE-P1"),
            "api_v1": None, "vermogen_w": None}
    try:
        dd = lees_json(info["url"], timeout=2)
        info["api_v1"] = True
        info["vermogen_w"] = dd.get("active_power_w") if isinstance(dd, dict) else None
    except urllib.error.HTTPError as e:
        info["api_v1"] = False if e.code == 403 else None
    except Exception:
        pass
    return info


def zoek_meters():
    """Zoek HomeWizard-toestellen op het eigen /24-net. Alleen een TCP-verbinding op
    poort 80 en daarna GET /api en GET /api/v1/data: er wordt niets naar een toestel geschreven."""
    netten = eigen_netten()
    kandidaten = [str(h) for n in netten for h in n.hosts()]
    with ThreadPoolExecutor(max_workers=64) as ex:
        open_ = [ip for ip, ok in zip(kandidaten, ex.map(_poort_open, kandidaten)) if ok]
    with ThreadPoolExecutor(max_workers=16) as ex:
        gevonden = [i for i in ex.map(_homewizard_info, open_) if i]
    gevonden.sort(key=lambda i: (not i["p1"], tuple(int(x) for x in i["ip"].split("."))))
    return {"netten": [str(n) for n in netten], "toestellen": gevonden}


# ---------- mail ----------

def stuur_mail(cfg, cfgpad, onderwerp, tekst, html_bijlage=None):
    """Verstuur een mail met de instellingen uit [mail] en HEMS_MAIL_WACHTWOORD uit .env.
    Geeft een uitzondering bij een fout (de oproeper beslist wat ermee te doen)."""
    import smtplib
    from email.message import EmailMessage
    host = cfg_str(cfg, "mail", "smtp_host")
    gebruiker = cfg_str(cfg, "mail", "gebruiker")
    if not host or not gebruiker:
        raise ValueError("mailserver of gebruiker ontbreekt")
    wachtwoord = lees_env(pad_in_project(cfgpad, ".env")).get("HEMS_MAIL_WACHTWOORD", "")
    msg = EmailMessage()
    msg["Subject"] = onderwerp
    msg["From"] = cfg_str(cfg, "mail", "van") or gebruiker
    msg["To"] = cfg_str(cfg, "mail", "naar") or gebruiker
    msg.set_content(tekst)
    if html_bijlage:
        msg.add_attachment(html_bijlage.encode("utf-8"), maintype="text", subtype="html", filename="rapport.html")
    poort = cfg_int(cfg, "mail", "smtp_poort", 465)
    if poort == 465:
        smtp = smtplib.SMTP_SSL(host, poort, timeout=20)
    else:
        smtp = smtplib.SMTP(host, poort, timeout=20)
        smtp.starttls()
    with smtp:
        if wachtwoord:
            smtp.login(gebruiker, wachtwoord)
        smtp.send_message(msg)


# ---------- tijd ----------

def utc_nu():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def tijdzone(cfg):
    naam = cfg_str(cfg, "algemeen", "tijdzone", "Europe/Brussels")
    if ZoneInfo:
        try:
            return ZoneInfo(naam)
        except Exception:
            pass
    return timezone.utc


def dag_grenzen_utc(datum, tz):
    """UTC-begin en -einde van een lokale kalenderdag (23/24/25 uur bij klokwissel)."""
    start_lokaal = datetime(datum.year, datum.month, datum.day, tzinfo=tz)
    einde_lokaal = start_lokaal + timedelta(days=1)
    return start_lokaal.astimezone(timezone.utc), einde_lokaal.astimezone(timezone.utc)


def is_dagtarief(dt_lokaal, cfg):
    if cfg_bool(cfg, "tarief", "weekend_als_nacht", True) and dt_lokaal.weekday() >= 5:
        return False
    start = cfg_int(cfg, "tarief", "dag_start_uur", 7)
    einde = cfg_int(cfg, "tarief", "dag_einde_uur", 22)
    return start <= dt_lokaal.hour < einde


# ---------- database ----------

def open_db(pad):
    os.makedirs(os.path.dirname(os.path.abspath(pad)), exist_ok=True)
    db = sqlite3.connect(pad, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def _tellerstand_op(db, ts_utc):
    return db.execute(
        "SELECT import_kwh, export_kwh FROM metingen WHERE ts<=? ORDER BY ts DESC LIMIT 1",
        (iso(ts_utc),)).fetchone()


def _sla_kwartier_op(db, start, einde):
    a = _tellerstand_op(db, start)
    b = _tellerstand_op(db, einde)
    if not a or not b or a[0] is None or b[0] is None:
        return False
    r = db.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(*) FROM metingen WHERE ts>? AND ts<=?",
        (iso(start), iso(einde))).fetchone()
    if not r or r[2] == 0:
        return False                # geen metingen in dit kwartier: later opnieuw proberen
    imp = b[0] - a[0]
    exp = b[1] - a[1]
    compleet = 1
    if parse_iso(r[0]) - start > timedelta(seconds=60) or einde - parse_iso(r[1]) > timedelta(seconds=60):
        compleet = 0                # gat aan het begin of einde van het kwartier
    gem = None
    if not (0 <= imp <= 50 and 0 <= exp <= 50):
        # teller teruggesprongen of onmogelijke sprong (meter vervangen/herstart):
        # bewaar het kwartier als gemarkeerd, maar zonder cijfers
        db.execute(
            "INSERT OR REPLACE INTO kwartieren(einde_utc, import_kwh, export_kwh, gemiddeld_w, compleet)"
            " VALUES(?,?,?,?,?)", (iso(einde), None, None, None, 0))
        return True
    gem = round(imp * 4 * 1000, 1)  # kWh per kwartier -> gemiddeld W
    db.execute(
        "INSERT OR REPLACE INTO kwartieren(einde_utc, import_kwh, export_kwh, gemiddeld_w, compleet)"
        " VALUES(?,?,?,?,?)",
        (iso(einde), round(imp, 4), round(exp, 4), gem, compleet))
    return True


def sluit_kwartieren(db):
    """Bereken alle afgeronde UTC-kwartieren die nog niet in de tabel staan.

    Kwartieren lopen op de klok; omdat de lokale tijd een geheel aantal uur
    van UTC verschilt, vallen de kwartiergrenzen op dezelfde tijdstippen.
    Sleutels zijn UTC, zodat het dubbele uur in oktober niet samensmelt.
    """
    row = db.execute("SELECT MAX(einde_utc) FROM kwartieren").fetchone()
    laatste = parse_iso(row[0]) if row and row[0] else None
    if laatste is None:
        r2 = db.execute("SELECT MIN(ts) FROM metingen").fetchone()
        if not r2 or not r2[0]:
            return 0
        eerste = parse_iso(r2[0])
        laatste = eerste.replace(minute=0, second=0, microsecond=0)
        while laatste <= eerste:
            laatste += timedelta(minutes=15)
    grens = utc_nu() - timedelta(seconds=5)
    aantal = 0
    while laatste + timedelta(minutes=15) <= grens:
        if _sla_kwartier_op(db, laatste, laatste + timedelta(minutes=15)):
            aantal += 1
        laatste += timedelta(minutes=15)
    db.commit()
    return aantal


def maandpiek(db, tz, jaar, maand):
    """Hoogste complete kwartierwaarde (W) van de lokale kalendermaand."""
    start_l = datetime(jaar, maand, 1, tzinfo=tz)
    einde_l = datetime(jaar + (maand == 12), maand % 12 + 1, 1, tzinfo=tz)
    start, einde = start_l.astimezone(timezone.utc), einde_l.astimezone(timezone.utc)
    r = db.execute(
        "SELECT MAX(gemiddeld_w) FROM kwartieren WHERE einde_utc>? AND einde_utc<=? AND compleet=1",
        (iso(start), iso(einde))).fetchone()
    return r[0] if r and r[0] is not None else 0.0


def lopend_kwartier(db):
    """(gemiddelde W tot nu, einde van het kwartier in UTC) op basis van de tellers."""
    nu = utc_nu()
    start = nu.replace(minute=(nu.minute // 15) * 15, second=0, microsecond=0)
    einde = start + timedelta(minutes=15)
    a = db.execute(
        "SELECT ts, import_kwh FROM metingen WHERE ts<=? ORDER BY ts DESC LIMIT 1",
        (iso(start),)).fetchone()
    b = db.execute(
        "SELECT ts, import_kwh FROM metingen ORDER BY ts DESC LIMIT 1").fetchone()
    if not a or not b or a[1] is None or b[1] is None:
        return None, einde
    verstreken = (parse_iso(b[0]) - parse_iso(a[0])).total_seconds()
    if verstreken < 30:
        return None, einde
    di = b[1] - a[1]
    if not 0 <= di <= 50:           # tellersprong of reset: geen betrouwbaar gemiddelde
        return None, einde
    return round(di * 1000 / (verstreken / 3600), 1), einde   # kWh tussen twee tellerstanden -> W


def dag_totalen(db, datum, tz, cfg):
    """Afname (dag/nacht), injectie en kosten van een lokale kalenderdag."""
    start, einde = dag_grenzen_utc(datum, tz)
    rows = db.execute(
        "SELECT ts, import_kwh, export_kwh FROM metingen WHERE ts>=? AND ts<? ORDER BY ts",
        (iso(start), iso(einde))).fetchall()
    laatste = db.execute(
        "SELECT import_kwh, export_kwh FROM metingen WHERE ts<? ORDER BY ts DESC LIMIT 1",
        (iso(start),)).fetchone()
    imp_dag = imp_nacht = exp = 0.0
    for ts, i, e in rows:
        if laatste and laatste[0] is not None:
            di = i - laatste[0]
            de = e - laatste[1]
            if 0 <= di < 50:                        # grotere sprong = tellerreset: niet meetellen
                if is_dagtarief(parse_iso(ts).astimezone(tz), cfg):
                    imp_dag += di
                else:
                    imp_nacht += di
            if 0 <= de < 50:
                exp += de
        laatste = (i, e)
    import ems_kosten                      # kosten zoals op een factuur (contract, net, capaciteit, btw)
    kosten = ems_kosten.kosten_dag(db, datum, tz, cfg)
    kost = kosten["totaal_eur"]
    return {
        "kosten": kosten,
        "import_dag_kwh": round(imp_dag, 3),
        "import_nacht_kwh": round(imp_nacht, 3),
        "export_kwh": round(exp, 3),
        "kosten_eur": round(kost, 2),
        "metingen": len(rows),
    }


# ---------- weergave ----------

def komma(x, dec=1):
    if x is None:
        return "-"
    return f"{x:.{dec}f}".replace(".", ",")


def euro(x):
    if x is None:
        return "-"
    return ("€ " + f"{x:.2f}").replace(".", ",")


def kw_w(x):
    """Watt naar leesbare tekst (W of kW met komma)."""
    if x is None:
        return "-"
    if abs(x) >= 1000:
        return komma(x / 1000, 2) + " kW"
    return komma(x, 0) + " W"


def svg_grafiek(punten, titel, tz=None, breedte=920, hoogte=280):
    """Eenvoudige lijngrafiek van vermogen (W) over een dag.

    punten: lijst van (datetime UTC, vermogen_w). Oranje boven nul = afname,
    blauw onder nul = teruglevering; kleuren zijn kleurenblindveilig en de
    nul-as is gestippeld zodat kleur nooit de enige informatie is.
    """
    if not punten:
        return ""
    waarden = [v for _, v in punten if v is not None]
    if not waarden:
        return ""
    top = max(max(waarden), 100)
    bodem = min(min(waarden), -100)
    # nette schaal
    def netjes(v):
        for stap in (100, 250, 500, 1000, 2000, 2500, 5000, 10000):
            if v <= stap:
                return stap
        return v
    top, bodem = netjes(top), -netjes(-bodem)
    t0, t1 = punten[0][0], punten[-1][0]
    span = max((t1 - t0).total_seconds(), 1)
    marge_l, marge_r, marge_b, marge_t = 55, 12, 26, 24
    pw = breedte - marge_l - marge_r
    ph = hoogte - marge_t - marge_b

    def xy(p):
        x = marge_l + (p[0] - t0).total_seconds() / span * pw
        y = marge_t + (top - p[1]) / (top - bodem) * ph
        return round(x, 1), round(y, 1)

    def poly(kleur, deel):
        pts = []
        for p in punten:
            if p[1] is None:
                continue
            v = p[1] if deel(p[1]) else 0.0
            pts.append("%s,%s" % xy((p[0], v)))
        if len(pts) < 2:
            return ""
        return (f'<polyline points="{" ".join(pts)}" fill="none" stroke="{kleur}" '
                f'stroke-width="1.6"/>')

    y0 = xy((t0, 0))[1]
    delen = []
    # rasterlijnen + y-labels
    for i in range(5):
        v = bodem + (top - bodem) * i / 4
        y = xy((t0, v))[1]
        delen.append(f'<line x1="{marge_l}" y1="{y}" x2="{breedte-marge_r}" y2="{y}" stroke="{KLEUR_RASTER}" stroke-width="0.6"/>')
        delen.append(f'<text x="{marge_l-6}" y="{y+4}" text-anchor="end" font-size="10" fill="{KLEUR_TEKST}">{komma(v/1000,1)}</text>')
    # x-labels: lokale uren
    eerste_uur = t0.replace(minute=0, second=0, microsecond=0)
    uur = eerste_uur
    while uur <= t1:
        if uur >= t0:
            x = xy((uur, 0))[0]
            lokaal_uur = uur.astimezone(tz).strftime("%H:%M") if tz else uur.strftime("%H:%M")
            delen.append(f'<text x="{x}" y="{hoogte-8}" text-anchor="middle" font-size="10" fill="{KLEUR_TEKST}">{lokaal_uur}</text>')
        uur += timedelta(hours=3)
    # nul-as (gestippeld)
    delen.append(f'<line x1="{marge_l}" y1="{y0}" x2="{breedte-marge_r}" y2="{y0}" stroke="{KLEUR_TEKST}" stroke-width="1" stroke-dasharray="4 3"/>')
    delen.append(poly(KLEUR_IMPORT, lambda v: v >= 0))
    delen.append(poly(KLEUR_EXPORT, lambda v: v < 0))
    delen.append(f'<text x="{marge_l}" y="14" font-size="12" font-weight="bold" fill="{KLEUR_TEKST}">{titel}</text>')
    delen.append(f'<text x="{breedte-marge_r}" y="14" text-anchor="end" font-size="11" fill="{KLEUR_TEKST}">kW  —  boven de stippellijn: afname, eronder: teruglevering</text>')
    return (f'<svg width="{breedte}" height="{hoogte}" viewBox="0 0 {breedte} {hoogte}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img">{"".join(delen)}</svg>')


# Oogje naast elk wachtwoordveld (wachtwoorden, tokens, API-sleutels): tonen/verbergen.
# Werkt ook voor velden die later door JavaScript gemaakt worden.
# ---------- versie en updates ----------

RELEASES_URL = "https://github.com/luc-vancraen/home-energy-management-system-installer/releases/latest"


def versie():
    """Geïnstalleerde versie: het bestand VERSIE dat de installer in de projectmap schrijft."""
    pad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSIE")
    try:
        with open(pad, encoding="utf-8") as f:
            v = f.read().strip()
        return v if re.fullmatch(r"\d+(\.\d+){1,3}", v) else "onbekend"
    except OSError:
        return "onbekend"


def _versie_tuple(v):
    return tuple(int(x) for x in v.split(".")) if re.fullmatch(r"\d+(\.\d+){1,3}", v or "") else None


_UPDATE = {"tijd": 0.0, "nieuwste": None, "bezig": False}
_UPDATE_SLOT = threading.Lock()


def _haal_nieuwste():
    try:
        # De pagina 'latest' stuurt door naar .../releases/tag/vX.Y.Z: zo is geen GitHub-API (en geen limiet) nodig.
        verzoek = urllib.request.Request(RELEASES_URL, method="HEAD", headers={"User-Agent": "hems-meting/1.0"})
        with urllib.request.urlopen(verzoek, timeout=15) as r:
            m = re.search(r"/tag/v?(\d+(?:\.\d+){1,3})$", r.geturl())
        nieuwste = m.group(1) if m else None
    except Exception:
        nieuwste = None                          # geen internet of GitHub onbereikbaar: gewoon niets tonen
    with _UPDATE_SLOT:
        _UPDATE.update(tijd=__import__("time").time(), bezig=False,
                       nieuwste=nieuwste or _UPDATE["nieuwste"])


def nieuwe_versie(cfg):
    """Geeft de nieuwere versie op GitHub terug (bv. "1.4.0"), of None. Blokkeert nooit: het opzoeken gebeurt
    hoogstens één keer per dag op de achtergrond; uit te zetten met [update] controleren = nee."""
    if not cfg_bool(cfg, "update", "controleren", True):
        return None
    import time as _t
    with _UPDATE_SLOT:
        if not _UPDATE["bezig"] and _t.time() - _UPDATE["tijd"] > 86400:
            _UPDATE["bezig"] = True
            threading.Thread(target=_haal_nieuwste, daemon=True).start()
        nieuwste = _UPDATE["nieuwste"]
    hier, daar = _versie_tuple(versie()), _versie_tuple(nieuwste)
    return nieuwste if hier and daar and daar > hier else None


# ---------- back-up van de metingen ----------

def zelfde_schijf(map_, cfgpad):
    """True als map_ op hetzelfde bestandssysteem ligt als het project: dan is een USB-stick of NAS niet gekoppeld
    en zou de 'extra kopie' gewoon op de eigen schijf belanden."""
    try:
        return os.stat(map_).st_dev == os.stat(pad_in_project(cfgpad, ".")).st_dev
    except OSError:
        return False


def backup_db(cfgpad, cfg, aantal=None):
    """Maakt een gecomprimeerde kopie van de databank in backup-db/ (de laatste 'aantal' blijven, standaard 7)
    en zet ze, als [opslag] backup_map ingevuld is, ook in die map (bv. een USB-stick of netwerkmap).
    Veilig terwijl het systeem meet (SQLite-backup). Geeft een korte melding terug."""
    import gzip
    if demo_actief(cfg):
        return "geen back-up: demo-modus"
    bron = db_pad(cfgpad, cfg)
    if not os.path.isfile(bron):
        return "geen back-up: nog geen databank"
    aantal = aantal or max(cfg_int(cfg, "opslag", "backup_aantal", 7), 1)
    doel_map = pad_in_project(cfgpad, "backup-db")
    os.makedirs(doel_map, exist_ok=True)
    naam = "ems-" + utc_nu().strftime("%Y-%m-%d") + ".db.gz"
    tmp_db = os.path.join(doel_map, ".bezig.db")
    tmp_gz = os.path.join(doel_map, ".bezig.db.gz")
    try:
        s = sqlite3.connect(f"file:{bron}?mode=ro", uri=True, timeout=30)
        d = sqlite3.connect(tmp_db)
        with d:
            s.backup(d)
        d.close()
        s.close()
        with open(tmp_db, "rb") as a, gzip.open(tmp_gz, "wb", compresslevel=6) as b:
            shutil.copyfileobj(a, b, 1 << 20)
        os.replace(tmp_gz, os.path.join(doel_map, naam))
    finally:
        for f in (tmp_db, tmp_gz):
            try:
                os.remove(f)
            except OSError:
                pass
    oud = sorted(f for f in os.listdir(doel_map) if re.fullmatch(r"ems-\d{4}-\d{2}-\d{2}\.db\.gz", f))
    for f in oud[:-aantal]:
        os.remove(os.path.join(doel_map, f))
    melding = f"back-up {naam} gemaakt in {doel_map}"
    extra = cfg_str(cfg, "opslag", "backup_map")
    if extra:
        try:
            if not os.path.isdir(extra):
                raise OSError("map niet gevonden (zit de USB-stick erin, is de netwerkmap gekoppeld?)")
            if zelfde_schijf(extra, cfgpad):
                raise OSError("de map ligt op de schijf van deze pc zelf (USB-stick of NAS niet gekoppeld?)")
            sub = os.path.join(extra, "hems-backup")
            os.makedirs(sub, exist_ok=True)
            shutil.copy2(os.path.join(doel_map, naam), os.path.join(sub, naam + ".bezig"))
            os.replace(os.path.join(sub, naam + ".bezig"), os.path.join(sub, naam))
            oud = sorted(f for f in os.listdir(sub) if re.fullmatch(r"ems-\d{4}-\d{2}-\d{2}\.db\.gz", f))
            for f in oud[:-aantal]:
                os.remove(os.path.join(sub, f))
            melding += f", kopie in {sub}"
        except OSError as e:
            melding += f"; LET OP: kopie naar {extra} mislukt: {e}"
    return melding


def laatste_backup(cfgpad):
    """Datum (JJJJ-MM-DD) van de nieuwste back-up in backup-db/, of None."""
    try:
        f = sorted(f for f in os.listdir(pad_in_project(cfgpad, "backup-db"))
                   if re.fullmatch(r"ems-\d{4}-\d{2}-\d{2}\.db\.gz", f))
    except OSError:
        return None
    return f[-1][4:14] if f else None


OOGJE = ("<style>.oogvak{display:flex;gap:6px;align-items:stretch;max-width:620px}"
         ".oogvak>input{flex:1;min-width:0;max-width:none!important;margin:0}"
         ".oogvak>button.oog{margin:0;padding:0 12px;font-size:1.1em;line-height:1;min-width:46px}</style>"
         "<script>(function(){function oog(r){(r.querySelectorAll?r:document).querySelectorAll("
         "'input[type=password]:not([data-oog])').forEach(function(i){i.setAttribute('data-oog','1');"
         "var v=document.createElement('span');v.className='oogvak';i.parentNode.insertBefore(v,i);v.appendChild(i);"
         "var b=document.createElement('button');b.type='button';b.className='oog';b.textContent='\\u{1F441}';"
         "b.title='Tonen';b.setAttribute('aria-label','Tonen');b.setAttribute('aria-pressed','false');"
         "b.onclick=function(){var t=i.type==='password';i.type=t?'text':'password';"
         "b.textContent=t?'\\u{1F648}':'\\u{1F441}';b.title=t?'Verbergen':'Tonen';"
         "b.setAttribute('aria-label',b.title);b.setAttribute('aria-pressed',t?'true':'false');i.focus()};"
         "v.appendChild(b)})}"
         "function start(){oog(document);new MutationObserver(function(m){m.forEach(function(x){"
         "x.addedNodes.forEach(function(n){if(n.nodeType===1)oog(n.parentNode||document)})})})"
         ".observe(document.body,{childList:true,subtree:true})}"
         "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start()})();"
         "</script>")
