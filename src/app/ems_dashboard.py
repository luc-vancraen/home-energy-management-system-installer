#!/usr/bin/env python3
"""ems_dashboard - webdashboard voor het HEMS-meetsysteem.

Toont live metingen, een daggrafiek, kwartierpieken, kosten volgens het
geconfigureerde tarief en (indien opgehaald) de marktprijzen. Alleen lezen:
het dashboard schrijft nooit naar een toestel. Zonder wachtwoord in de config
is het enkel bedoeld voor het eigen thuisnetwerk.

De pagina Instellingen (/instellingen) wijzigt alleen de eigen configuratie
(config/ems.ini, met back-up) en .env. Ze vraagt een beheerwachtwoord; het
eerste wachtwoord kies je met de eenmalige instelcode uit config/instelcode.
"""
import argparse
import hmac
import html
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ems_common as ec
import ems_instellingen as ei
import ems_vragenlijst as ev
import ems_kosten

CFG = None
CFG_PAD = None
CFG_MTIME = None
DB_PAD = None
RAPPORT_MAP = None
TZ = timezone.utc
_CFG_SLOT = threading.Lock()

SESSIE_DUUR_S = 3600
_SESSIES = {}                 # token -> [verloopt (monotonic), csrf-token]
_SESSIE_SLOT = threading.Lock()
_MISLUKT = []                 # tijdstippen van mislukte aanmeldingen (afremmen)
_KIJK_OK = {}                 # Authorization-kop -> hash waarmee hij klopte (pbkdf2 niet bij elke opvraging)


STIJL = """
body{font-family:sans-serif;color:#222;max-width:980px;margin:0 auto;padding:12px;background:#fafafa}
h1{font-size:1.5em;margin:.2em 0}
h2{font-size:1.1em;margin:1em 0 .3em}
.kaarten{display:flex;flex-wrap:wrap;gap:10px}
.kaart{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:150px;flex:1}
.kaart .groot{font-size:1.7em;font-weight:bold}
.kaart .label{font-size:.85em;color:#555}
.oranje{color:#E69F00}.blauw{color:#0072B2}
table{border-collapse:collapse;width:100%;background:#fff;font-size:.9em}
th,td{border:1px solid #ddd;padding:4px 8px;text-align:right}
th:first-child,td:first-child{text-align:left}
.ok{background:#e7f4e7}.fout{background:#fde3e3}
.balk{height:14px;background:#eee;border-radius:7px;overflow:hidden}
.balk>div{height:100%;background:#E69F00}
.nav a{margin-right:14px}
.voet{margin-top:24px;font-size:.8em;color:#777}
.paneel{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:10px 0}
.paneel h2{margin-top:.2em}
label{display:block;margin:.6em 0 .2em;font-weight:bold;font-size:.92em}
input[type=text],input[type=password],select{width:100%;max-width:520px;box-sizing:border-box;padding:7px;font-size:1em;border:1px solid #bbb;border-radius:6px}
button{padding:7px 14px;font-size:1em;border-radius:6px;border:1px solid #888;background:#f2f2f2;cursor:pointer;margin:6px 6px 0 0}
button.hoofd{background:#0072B2;color:#fff;border-color:#0072B2}
button:disabled{opacity:.5;cursor:wait}
.uitleg{font-size:.85em;color:#555;margin:.2em 0}
.melding{padding:8px 10px;border-radius:6px;margin:8px 0;display:none}
.melding.ok,.melding.fout{display:block}
svg{background:#fff;border:1px solid #ddd;border-radius:8px;max-width:100%}
@media(max-width:640px){.kaart{min-width:44%}}
"""


def herlaad_config(forceer=False):
    """Lees ems.ini opnieuw als ze gewijzigd is (bv. via Instellingen); geen herstart nodig."""
    global CFG, CFG_MTIME, TZ, DB_PAD, RAPPORT_MAP
    m = ec.config_mtime(CFG_PAD)
    if not forceer and m == CFG_MTIME:
        return
    with _CFG_SLOT:
        try:
            nieuw = ec.laad_config(CFG_PAD)
        except Exception as e:
            print(f"waarschuwing: configuratie onleesbaar, oude blijft actief: {e}", file=sys.stderr)
            CFG_MTIME = m
            return
        CFG = nieuw
        TZ = ec.tijdzone(CFG)
        DB_PAD = ec.db_pad(CFG_PAD, CFG)
        RAPPORT_MAP = ec.pad_in_project(CFG_PAD, ec.cfg_str(CFG, "rapport", "map", "rapporten"))
        CFG_MTIME = m


def db():
    return ec.open_db(DB_PAD)


def laatste_meting(conn):
    return conn.execute(
        "SELECT ts, vermogen_w, l1_w, l2_w, l3_w, l1_a, l2_a, l3_a, l1_v, l2_v, l3_v,"
        " import_kwh, export_kwh, meter_kwartier_w, meter_maandpiek_w"
        " FROM metingen ORDER BY ts DESC LIMIT 1").fetchone()


def budget_w(conn, cfg):
    nu = ec.utc_nu()
    piek = ec.maandpiek(conn, TZ, nu.astimezone(TZ).year, nu.astimezone(TZ).month)
    minimum = ec.cfg_float(cfg, "installatie", "minimum_piek_kw", 2.5) * 1000
    return max(minimum, piek), piek


def status_json():
    conn = db()
    m = laatste_meting(conn)
    if not m:
        return {"status": "geen_data"}
    ts = ec.parse_iso(m[0])
    leeftijd = (ec.utc_nu() - ts).total_seconds()
    gem, einde = ec.lopend_kwartier(conn)
    budget, piek = budget_w(conn, CFG)
    kw_einde_lokaal = einde.astimezone(TZ).strftime("%H:%M")
    conn.close()
    return {
        "status": "ok" if leeftijd <= 30 else "stil",
        "ts_utc": m[0], "leeftijd_s": round(leeftijd),
        "vermogen_w": m[1],
        "fasen_w": [m[2], m[3], m[4]],
        "fasen_a": [m[5], m[6], m[7]],
        "fasen_v": [m[8], m[9], m[10]],
        "kwartier_gemiddeld_w": gem,
        "kwartier_einde_lokaal": kw_einde_lokaal,
        "budget_w": budget,
        "resterend_w": None if gem is None else round(budget - gem),
        "maandpiek_w": piek,
    }


def prijzen_van_dag(conn, datum):
    start, einde = ec.dag_grenzen_utc(datum, TZ)
    rows = conn.execute(
        "SELECT start_utc, eur_kwh FROM prijzen WHERE start_utc>=? AND start_utc<? ORDER BY start_utc",
        (ec.iso(start), ec.iso(einde))).fetchall()
    per_uur = {}
    for ts, p in rows:
        uur = ec.parse_iso(ts).astimezone(TZ).strftime("%H:00")
        per_uur.setdefault(uur, []).append(p)
    return [(u, sum(v) / len(v), min(v), max(v)) for u, v in sorted(per_uur.items())]


_MAAND_CACHE = {}


def maand_tot_nu(datum):
    """Kosten van de 1e van de maand t/m datum; afgesloten dagen worden onthouden."""
    conn = db()
    tot = 0.0
    d = datum.replace(day=1)
    vandaag = ec.utc_nu().astimezone(TZ).date()
    while d <= datum:
        sleutel = (DB_PAD, d, CFG_MTIME)
        if sleutel in _MAAND_CACHE:
            tot += _MAAND_CACHE[sleutel]
        else:
            k = ems_kosten.kosten_dag(conn, d, TZ, CFG)["totaal_eur"]
            if d < vandaag:
                _MAAND_CACHE[sleutel] = k
            tot += k
        d += timedelta(days=1)
    conn.close()
    return round(tot, 2)


def tarief_ingevuld():
    c = ems_kosten.contract(CFG)
    return bool(c["dag"] or c["blok_prijs"] or c["soort"] in ("variabel", "dynamisch"))


def kaart(label, waarde, eenheid="", klasse=""):
    return (f'<div class="kaart"><div class="label">{label}</div>'
            f'<div class="groot {klasse}">{waarde}</div><div class="label">{eenheid}</div></div>')


def dag_pagina(datum):
    conn = db()
    t = ec.dag_totalen(conn, datum, TZ, CFG)
    start, einde = ec.dag_grenzen_utc(datum, TZ)
    rows = conn.execute(
        "SELECT ts, vermogen_w FROM metingen WHERE ts>=? AND ts<? ORDER BY ts",
        (ec.iso(start), ec.iso(einde))).fetchall()
    stap = max(1, len(rows) // 800)
    punten = [(ec.parse_iso(ts), v) for ts, v in rows[::stap] if v is not None]
    grafiek = ec.svg_grafiek(punten, f"Vermogen op {datum.strftime('%d/%m/%Y')}", tz=TZ) if punten else "<p>Nog geen metingen voor deze dag.</p>"
    kw = conn.execute(
        "SELECT einde_utc, gemiddeld_w, import_kwh, export_kwh, compleet FROM kwartieren"
        " WHERE einde_utc>? AND einde_utc<=? ORDER BY einde_utc DESC",
        (ec.iso(start), ec.iso(einde))).fetchall()
    prijzen = prijzen_van_dag(conn, datum)
    conn.close()

    is_vandaag = datum == ec.utc_nu().astimezone(TZ).date()
    gister = datum - timedelta(days=1)
    morgen = datum + timedelta(days=1)
    h = [f"<html><head><meta charset='utf-8'><title>HEMS {datum.strftime('%d/%m/%Y')}</title>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         f"<style>{STIJL}</style></head><body>"]
    h.append("<h1>HEMS — energieoverzicht</h1>")
    if ec.demo_actief(CFG):
        h.append("<p class='fout' style='padding:8px'><strong>DEMO-MODUS:</strong> dit zijn gesimuleerde metingen, "
                 "niet die van je meter. Stel je meter in via <a href='/instellingen'>Instellingen</a>.</p>")
    h.append(f"<p class='nav'><a href='/dag/{gister}'>&larr; {gister.strftime('%d/%m')}</a>"
             f"<a href='/'>vandaag</a>"
             f"<a href='/dag/{morgen}'>{morgen.strftime('%d/%m')} &rarr;</a>"
             f"<a href='/rapporten'>rapporten</a>"
             f"<a href='/dag/{datum}.csv'>CSV van deze dag</a>"
             f"<a href='/instellingen'>instellingen</a></p>")

    if is_vandaag:
        h.append("<h2>Nu</h2><div class='kaarten' id='live'>"
                 + kaart("Vermogen", '<span id="v">…</span>', "", "")
                 + kaart("Dit kwartier (gem.)", '<span id="k">…</span>', "")
                 + kaart("Resterend in budget", '<span id="r">…</span>', "")
                 + kaart("Maandpiek", '<span id="m">…</span>', "")
                 + "</div><p id='status' class='label'>…</p>"
                 + """<script>
function komma(x,d){return x==null?'-':x.toFixed(d).replace('.',',')}
function kw(x){if(x==null)return '-';return Math.abs(x)>=1000?komma(x/1000,2)+' kW':komma(x,0)+' W'}
function tick(){fetch('/api/now').then(r=>r.json()).then(j=>{
 if(j.status==='geen_data'){document.getElementById('status').textContent='Nog geen metingen ontvangen.';return}
 let v=j.vermogen_w;
 document.getElementById('v').textContent=kw(Math.abs(v))+(v<0?' teruglevering':' afname');
 document.getElementById('k').textContent=kw(j.kwartier_gemiddeld_w)+' (einde '+j.kwartier_einde_lokaal+')';
 document.getElementById('r').textContent=kw(j.resterend_w)+' van '+kw(j.budget_w);
 document.getElementById('m').textContent=kw(j.maandpiek_w);
 let s=document.getElementById('status');
 if(j.status==='ok'){s.textContent='Metingen vers ('+j.leeftijd_s+' s oud, '+j.ts_utc+').';s.className='ok'}
 else{s.textContent='LET OP: laatste meting is '+j.leeftijd_s+' s oud — is de meterdaemon actief?';s.className='fout'}
}).catch(e=>{document.getElementById('status').textContent='status ophalen mislukt: '+e})}
tick();setInterval(tick,5000);setTimeout(()=>location.reload(),300000);
</script>""")

    h.append("<h2>Totaal " + datum.strftime("%d/%m/%Y") + "</h2><div class='kaarten'>"
             + kaart("Afname dagtarief", ec.komma(t["import_dag_kwh"], 2), "kWh")
             + kaart("Afname nachttarief", ec.komma(t["import_nacht_kwh"], 2), "kWh")
             + kaart("Teruglevering", ec.komma(t["export_kwh"], 2), "kWh", "blauw")
             + kaart("Kosten " + ("vandaag" if is_vandaag else "die dag"), ec.euro(t["kosten_eur"]),
                     "alles inbegrepen, zie hieronder")
             + kaart("Deze maand tot nu", ec.euro(maand_tot_nu(datum)), "zelfde berekening, per dag opgeteld")
             + "</div>")
    if not tarief_ingevuld():
        h.append("<p class='uitleg'>Je contract is nog niet ingevuld, de kosten kloppen dus niet: "
                 "<a href='/instellingen#tarief'>vul het in bij Instellingen</a>.</p>")
    h.append(grafiek)

    if kw:
        h.append("<h2>Kwartieren (nieuwste bovenaan)</h2><table>"
                 "<tr><th>Kwartier eindigt</th><th>Gemiddeld</th><th>Afname</th><th>Teruglevering</th><th>Volledig?</th></tr>")
        for einde_utc, gem, imp, exp, compleet in kw:
            lbl = ec.parse_iso(einde_utc).astimezone(TZ).strftime("%d/%m %H:%M")
            h.append(f"<tr{' class=fout' if not compleet else ''}><td>{lbl}</td><td>{ec.kw_w(gem)}</td>"
                     f"<td>{ec.komma(imp,3)} kWh</td><td>{ec.komma(exp,3)} kWh</td>"
                     f"<td>{'ja' if compleet else 'nee — gaten in de data'}</td></tr>")
        h.append("</table>")
    h.append("<h2>Hoe de kosten van " + datum.strftime("%d/%m") + " opgebouwd zijn</h2>"
             + ems_kosten.tabel_html(t["kosten"])
             + "<p class='uitleg'>Zoals op een factuur: energie volgens je contract, netkosten en heffingen, "
               "capaciteitstarief en vaste kosten per dag, btw, min de vergoeding voor teruglevering. "
               "Vergelijk af en toe met je echte factuur.</p>")
    if prijzen:
        h.append("<h2>Marktprijs per uur (gemiddeld, kale marktprijs)</h2><table>"
                 "<tr><th>Uur</th><th>Gemiddeld</th><th>Min</th><th>Max</th></tr>")
        for uur, gem, lo, hi in prijzen:
            h.append(f"<tr><td>{uur}</td><td>{ec.komma(gem*100,2)} c€/kWh</td>"
                     f"<td>{ec.komma(lo*100,2)}</td><td>{ec.komma(hi*100,2)}</td></tr>")
        h.append("</table>")
    h.append("<p class='voet'>HEMS-meetsysteem — alleen meten, geen sturing. Tijden in lokale tijd; "
             "opslag in UTC. Kleuren zijn kleurenblindveilig; de stippellijn is 0 W.</p>"
             "<p class='voet'>Copyright (c) 2026 luc-vancraen</p></body></html>")
    return "".join(h)


def csv_van_dag(datum):
    conn = db()
    start, einde = ec.dag_grenzen_utc(datum, TZ)
    rows = conn.execute(
        "SELECT ts, vermogen_w, import_kwh, export_kwh, l1_w, l2_w, l3_w FROM metingen"
        " WHERE ts>=? AND ts<? ORDER BY ts", (ec.iso(start), ec.iso(einde))).fetchall()
    conn.close()
    uit = ["tijdstip;vermogen_W;teller_afname_kWh;teller_injectie_kWh;L1_W;L2_W;L3_W"]
    for ts, v, i, e, l1, l2, l3 in rows:
        lokaal = ec.parse_iso(ts).astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S")
        def c(x, d=1):
            return "" if x is None else f"{x:.{d}f}".replace(".", ",")
        uit.append(";".join([lokaal, c(v), c(i, 3), c(e, 3), c(l1), c(l2), c(l3)]))
    return "\n".join(uit)


def rapporten_lijst():
    h = [f"<html><head><meta charset='utf-8'><title>HEMS rapporten</title><style>{STIJL}</style></head><body>"]
    h.append("<h1>Dagrapporten</h1><p class='nav'><a href='/'>&larr; dashboard</a></p>")
    bestanden = []
    if os.path.isdir(RAPPORT_MAP):
        bestanden = sorted((f for f in os.listdir(RAPPORT_MAP) if f.endswith(".html")), reverse=True)
    if not bestanden:
        h.append("<p>Nog geen rapporten. De rapporttimer maakt er elke ochtend een.</p>")
    for f in bestanden:
        h.append(f"<p><a href='/rapporten/{f}'>{f[:-5]}</a></p>")
    h.append("<p class='voet'>Copyright (c) 2026 luc-vancraen</p></body></html>")
    return "".join(h)


# ---------- pagina Instellingen ----------

def _env_pad():
    return ec.pad_in_project(CFG_PAD, ".env")


def beheer_hash():
    return ec.lees_env(_env_pad()).get("HEMS_BEHEER_WACHTWOORD", "")


def _nieuwe_sessie():
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with _SESSIE_SLOT:
        nu = time.monotonic()
        for t in [t for t, v in _SESSIES.items() if v[0] < nu]:
            del _SESSIES[t]
        _SESSIES[token] = [nu + SESSIE_DUUR_S, csrf]
    return token


def _te_veel_pogingen():
    nu = time.monotonic()
    _MISLUKT[:] = [t for t in _MISLUKT if nu - t < 300]
    return len(_MISLUKT) >= 10


def _pagina(titel, inhoud):
    return (f"<html><head><meta charset='utf-8'><title>{titel}</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{STIJL}</style></head><body><h1>{titel}</h1>"
            "<p class='nav'><a href='/'>&larr; dashboard</a></p>"
            f"{inhoud}<p class='voet'>HEMS-meetsysteem — Instellingen wijzigen alleen de eigen "
            "configuratie van dit systeem, nooit een toestel. Copyright (c) 2026 luc-vancraen</p></body></html>")


def eerste_keer_pagina(code="", fout=""):
    f = f"<p class='melding fout'>{html.escape(fout)}</p>" if fout else ""
    return _pagina("HEMS — eerste keer instellen", f"""{f}
<div class='paneel'><h2>Kies een beheerwachtwoord</h2>
<p class='uitleg'>Met dit wachtwoord kan je later de instellingen wijzigen. De <b>instelcode</b> toonde de
installer op het einde; je vindt ze ook in <code>config/instelcode</code> in de projectmap.</p>
<form method='post' action='/instellingen/eerste'>
<label for='code'>Instelcode</label><input type='text' id='code' name='code' value='{html.escape(code)}' autocomplete='off' placeholder='XXXX-XXXX'>
<label for='w1'>Nieuw beheerwachtwoord (minstens 8 tekens)</label><input type='password' id='w1' name='w1' autocomplete='new-password'>
<label for='w2'>Nog eens</label><input type='password' id='w2' name='w2' autocomplete='new-password'>
<br><button class='hoofd' type='submit'>Opslaan en verder</button>
</form></div>""")


def aanmeld_pagina(fout=""):
    f = f"<p class='melding fout'>{html.escape(fout)}</p>" if fout else ""
    return _pagina("HEMS — aanmelden", f"""{f}
<div class='paneel'><h2>Aanmelden voor Instellingen</h2>
<form method='post' action='/instellingen/aanmelden'>
<label for='w'>Beheerwachtwoord</label><input type='password' id='w' name='w' autocomplete='current-password' autofocus>
<br><button class='hoofd' type='submit'>Aanmelden</button>
</form>
<p class='uitleg'>Wachtwoord vergeten? Verwijder de regel <code>HEMS_BEHEER_WACHTWOORD</code> uit <code>.env</code>
in de projectmap; daarna kies je een nieuw wachtwoord met de instelcode uit <code>config/instelcode</code>.</p></div>""")


def _check_nieuw_wachtwoord(w1, w2):
    if len(w1) < 8:
        return "Het wachtwoord moet minstens 8 tekens hebben."
    if w1 != w2:
        return "De twee wachtwoorden zijn niet gelijk."
    return ""


class Handler(BaseHTTPRequestHandler):
    def _stuur(self, tekst, type="text/html; charset=utf-8", code=200, koppen=()):
        data = tekst.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.path.startswith("/instellingen"):
            self.send_header("Cache-Control", "no-store")
        for k, v in koppen:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._stuur(json.dumps(obj), "application/json", code)

    def _naar(self, pad, koppen=()):
        self.send_response(303)
        self.send_header("Location", pad)
        self.send_header("Content-Length", "0")
        for k, v in koppen:
            self.send_header(k, v)
        self.end_headers()

    def _sessie(self):
        """[verloopt, csrf] van een geldige sessie, of None. Verlengt de sessie bij gebruik."""
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            token = c["hems_sessie"].value if "hems_sessie" in c else ""
        except Exception:
            return None
        with _SESSIE_SLOT:
            s = _SESSIES.get(token)
            if not s or s[0] < time.monotonic():
                _SESSIES.pop(token, None)
                return None
            s[0] = time.monotonic() + SESSIE_DUUR_S
            return s

    def _cookie(self, token, duur=SESSIE_DUUR_S):
        return ("Set-Cookie", f"hems_sessie={token}; Path=/instellingen; HttpOnly; SameSite=Strict; Max-Age={duur}")

    def _lees_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20000:
            raise ValueError("te groot")
        rauw = self.rfile.read(n).decode("utf-8") if n else ""
        if self.headers.get("Content-Type", "").startswith("application/json"):
            d = json.loads(rauw or "{}")
            return d if isinstance(d, dict) else {}
        return {k: v[0] for k, v in parse_qs(rauw).items()}

    def _zelfde_oorsprong(self):
        """Weiger formulieren die van een andere website komen (naast SameSite-cookie en CSRF-token)."""
        oorsprong = self.headers.get("Origin")
        if not oorsprong or oorsprong == "null":
            return oorsprong is None
        return urlparse(oorsprong).netloc == self.headers.get("Host", "")

    def do_POST(self):
        herlaad_config()
        if not self._toegang():
            return
        pad = urlparse(self.path).path
        try:
            if not pad.startswith("/instellingen/") or not self._zelfde_oorsprong():
                return self._stuur("niet toegelaten", code=403)
            data = self._lees_body()
            if pad == "/instellingen/eerste":
                return self._post_eerste(data)
            if pad == "/instellingen/aanmelden":
                return self._post_aanmelden(data)
            s = self._sessie()
            csrf = self.headers.get("X-HEMS-CSRF") or data.get("csrf", "")
            if not s or not hmac.compare_digest(str(csrf), s[1]):
                return self._json({"ok": False, "fout": "Niet aangemeld of sessie verlopen."}, 401)
            if pad == "/instellingen/afmelden":
                with _SESSIE_SLOT:
                    for t in [t for t, v in _SESSIES.items() if v is s]:
                        del _SESSIES[t]
                return self._naar("/instellingen", [self._cookie("", 0)])
            if pad == "/instellingen/zoek":
                return self._json(ec.zoek_meters())
            if pad == "/instellingen/test":
                return self._json(ec.test_meter(str(data.get("url", "")).strip()))
            if pad == "/instellingen/opslaan":
                return self._json(self._opslaan(data))
            if pad == "/instellingen/vl-opslaan":
                r = ev.opslaan(str(data.get("sectie", "")), data.get("waarden"), CFG, CFG_PAD, DB_PAD)
                r["overzicht"] = ev.overzicht(CFG_PAD)
                r["waarden"] = ev.waarden(CFG_PAD)
                return self._json(r)
            if pad == "/instellingen/vl-zoek":
                r = ev.zoek_toestellen(CFG, CFG_PAD, DB_PAD)
                r["overzicht"] = ev.overzicht(CFG_PAD)
                return self._json(r)
            if pad == "/instellingen/prijstest":
                r = ei.prijstest(CFG, CFG_PAD)
                r["overzicht"] = ei.overzicht(CFG, CFG_PAD, DB_PAD)
                return self._json(r)
            if pad == "/instellingen/maandindex":
                r = ei.maandindex_opslaan(data, DB_PAD)
                return self._json(r)
            if pad == "/instellingen/mailtest":
                return self._json(ei.mailtest(CFG, CFG_PAD))
            if pad == "/instellingen/wachtwoord":
                return self._json(self._wijzig_wachtwoord(data))
            return self._stuur("niet gevonden", code=404)
        except Exception as e:
            print(f"fout bij {pad}: {e}", file=sys.stderr)
            return self._json({"ok": False, "fout": f"interne fout: {e}"}, 500)

    def _post_eerste(self, data):
        if beheer_hash():
            return self._naar("/instellingen")
        code = ec.instelcode(CFG_PAD, maak=False)
        gegeven = str(data.get("code", "")).strip().upper()
        if _te_veel_pogingen():
            return self._stuur(eerste_keer_pagina(gegeven, "Te veel mislukte pogingen. Wacht 5 minuten."), code=429)
        if not code or not hmac.compare_digest(gegeven, code):
            _MISLUKT.append(time.monotonic())
            time.sleep(1)
            return self._stuur(eerste_keer_pagina(gegeven, "De instelcode klopt niet."), code=403)
        fout = _check_nieuw_wachtwoord(str(data.get("w1", "")), str(data.get("w2", "")))
        if fout:
            return self._stuur(eerste_keer_pagina(gegeven, fout), code=400)
        ec.zet_env(_env_pad(), "HEMS_BEHEER_WACHTWOORD", ec.hash_wachtwoord(str(data["w1"])))
        try:
            os.remove(ec.instelcode_pad(CFG_PAD))
        except OSError:
            pass
        print("beheerwachtwoord ingesteld; instelcode vervallen")
        return self._naar("/instellingen", [self._cookie(_nieuwe_sessie())])

    def _post_aanmelden(self, data):
        h = beheer_hash()
        if not h:
            return self._naar("/instellingen")
        if _te_veel_pogingen():
            return self._stuur(aanmeld_pagina("Te veel mislukte pogingen. Wacht 5 minuten."), code=429)
        if not ec.check_wachtwoord(str(data.get("w", "")), h):
            _MISLUKT.append(time.monotonic())
            time.sleep(1)
            return self._stuur(aanmeld_pagina("Verkeerd wachtwoord."), code=403)
        return self._naar("/instellingen", [self._cookie(_nieuwe_sessie())])

    def _opslaan(self, data):
        r = ei.opslaan(str(data.get("sectie", "")), data.get("waarden"), CFG, CFG_PAD, DB_PAD)
        herlaad_config(forceer=True)
        r["overzicht"] = ei.overzicht(CFG, CFG_PAD, DB_PAD)
        r["waarden"] = ei.waarden(CFG, CFG_PAD)
        r["fasen_meter"] = ei.fasen_uit_meter(DB_PAD) if not ec.demo_actief(CFG) else None
        if r.get("herstart"):
            if os.environ.get("INVOCATION_ID"):      # draait onder systemd (Restart=always): stoppen = herstarten
                print("dashboard herstart voor nieuwe poort/bind")
                threading.Timer(1.5, lambda: os._exit(0)).start()
            else:
                r["herstart"] = False
                r["bericht"] = r["bericht"].replace(" Het dashboard herstart nu.", "") + \
                    " Herstart het dashboard zelf om de nieuwe poort te gebruiken."
        return r

    def _wijzig_wachtwoord(self, data):
        if not ec.check_wachtwoord(str(data.get("oud", "")), beheer_hash()):
            time.sleep(1)
            return {"ok": False, "fout": "Het huidige wachtwoord klopt niet."}
        fout = _check_nieuw_wachtwoord(str(data.get("n1", "")), str(data.get("n2", "")))
        if fout:
            return {"ok": False, "fout": fout}
        ec.zet_env(_env_pad(), "HEMS_BEHEER_WACHTWOORD", ec.hash_wachtwoord(str(data["n1"])))
        return {"ok": True}

    def _toegang(self):
        wachtwoord = ec.cfg_str(CFG, "dashboard", "wachtwoord")
        h = ec.lees_env(_env_pad()).get("HEMS_DASHBOARD_WACHTWOORD", "")
        if not wachtwoord and not h:
            return True
        kop = self.headers.get("Authorization", "")
        if kop.startswith("Basic "):
            import base64
            try:
                gebruiker, _, ww = base64.b64decode(kop[6:]).decode("utf-8").partition(":")
                if h:
                    if _KIJK_OK.get(kop) == h:
                        return True
                    if ec.check_wachtwoord(ww, h):
                        if len(_KIJK_OK) > 50:
                            _KIJK_OK.clear()
                        _KIJK_OK[kop] = h
                        return True
                elif hmac.compare_digest(ww, wachtwoord):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="HEMS"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        herlaad_config()
        if not self._toegang():
            return
        deel = urlparse(self.path)
        pad = deel.path
        try:
            if pad == "/instellingen":
                if not beheer_hash():
                    ec.instelcode(CFG_PAD)          # maakt de code aan als ze nog niet bestaat
                    code = parse_qs(deel.query).get("code", [""])[0]
                    return self._stuur(eerste_keer_pagina(code))
                s = self._sessie()
                if not s:
                    return self._stuur(aanmeld_pagina())
                return self._stuur(ei.pagina(s[1], CFG, CFG_PAD, DB_PAD, STIJL))
            if pad in ("/instellingen/vragenlijst", "/instellingen/vragenlijst/mijn-installatie.md"):
                s = self._sessie() if beheer_hash() else None
                if not s:
                    return self._naar("/instellingen")
                if pad.endswith(".md"):
                    try:
                        with open(ev.uitvoer_pad(CFG_PAD), encoding="utf-8") as f:
                            tekst = f.read()
                    except OSError:
                        tekst = ev.markdown(CFG, CFG_PAD, DB_PAD)
                    return self._stuur(tekst, "text/markdown; charset=utf-8")
                return self._stuur(ev.pagina(s[1], CFG, CFG_PAD, DB_PAD, STIJL))
            if pad == "/":
                self._stuur(dag_pagina(ec.utc_nu().astimezone(TZ).date()))
            elif pad == "/api/now":
                self._stuur(json.dumps(status_json()), "application/json")
            elif pad == "/rapporten":
                self._stuur(rapporten_lijst())
            elif pad.startswith("/rapporten/"):
                naam = os.path.basename(pad[len("/rapporten/"):])
                volledig = os.path.join(RAPPORT_MAP, naam)
                if os.path.isfile(volledig) and naam.endswith(".html"):
                    with open(volledig, encoding="utf-8") as f:
                        self._stuur(f.read())
                else:
                    self._stuur("niet gevonden", code=404)
            elif pad.startswith("/dag/"):
                rest = pad[len("/dag/"):]
                als_csv = rest.endswith(".csv")
                if als_csv:
                    rest = rest[:-4]
                datum = datetime.strptime(rest, "%Y-%m-%d").date()
                if als_csv:
                    self._stuur(csv_van_dag(datum), "text/csv; charset=utf-8")
                else:
                    self._stuur(dag_pagina(datum))
            else:
                self._stuur("niet gevonden", code=404)
        except ValueError:
            self._stuur("ongeldige datum", code=400)
        except Exception as e:
            self._stuur(f"interne fout: {e}", code=500)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    global CFG_PAD
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="HEMS-dashboard (alleen lezen)")
    ap.add_argument("--config", required=True, help="pad naar ems.ini")
    args = ap.parse_args()
    CFG_PAD = os.path.abspath(args.config)
    herlaad_config(forceer=True)
    if not beheer_hash():
        ec.instelcode(CFG_PAD)
        print(f"nog geen beheerwachtwoord: open /instellingen met de code uit {ec.instelcode_pad(CFG_PAD)}")
    poort = ec.cfg_int(CFG, "dashboard", "poort", 8080)
    bind = ec.cfg_str(CFG, "dashboard", "bind", "0.0.0.0")
    server = ThreadingHTTPServer((bind, poort), Handler)
    print(f"ems_dashboard luistert op http://{bind}:{poort} (tijdzone {TZ})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
