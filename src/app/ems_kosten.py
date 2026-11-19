"""ems_kosten - kosten per dag, opgebouwd zoals op een factuur.

Onderdelen (alles instelbaar op de pagina Instellingen -> Contract en tarief):
  - Energie van de leverancier, volgens het soort contract:
      enkel      één vaste prijs per kWh
      dagnacht   vaste dag- en nachtprijs
      variabel   factor x maandindex + toeslag (bv. 1,05 x Belpex-RLP-M + 15 EUR/MWh)
      dynamisch  factor x marktprijs van het kwartier/uur + toeslag
      blok       een vast blok kWh per jaar aan een vaste prijs, over de maanden verdeeld
                 (zonneprofiel SPP of naar het aantal dagen), daarboven variabel; ongebruikt
                 blok schuift wel of niet door (zoals EnergyVision "1.000 kWh vast")
  - Netkosten en heffingen per kWh afname (distributie, groenestroom, WKK, energiebijdrage, accijns)
  - Capaciteitstarief: EUR/kW/jaar op het gemiddelde van de maandpieken van de laatste 12 maanden
    (elke maand minstens het minimum, Vlaanderen 2,5 kW), per dag 1/365
  - Vaste kosten per jaar (databeheer, vaste vergoeding leverancier), per dag 1/365
  - Btw op dit alles (instelbaar, standaard 6 %)
  - Teruglevering: vaste vergoeding of factor x maandindex/marktprijs + toeslag (met minimum), zonder btw

Maandindex: de officiële waarde als die is ingevuld (tabel maandindex), anders een SCHATTING:
het gemiddelde van de marktprijzen van die maand, gewogen naar je eigen afname (voor afname) of
je eigen teruglevering (voor injectie). Dat benadert de RLP-/SPP-weging van de officiële index.
Alle bedragen worden intern exclusief btw gerekend.
"""
import bisect
import calendar
from datetime import datetime, timedelta, timezone

import ems_common as ec

# Verdeling van 1.000 kWh over de maanden volgens het Synergrid SPP-profiel 2026 (zonneprofiel).
# Bevestigd met echte facturen van een blokcontract. Voor andere jaren
# wordt dezelfde vorm gebruikt; het profiel verschuift jaarlijks maar licht.
SPP_2026 = [20.23, 43.70, 86.60, 117.62, 142.34, 141.99, 137.67, 118.65, 89.34, 60.21, 28.34, 13.31]

# Voorbeeldwaarden (excl. btw), Fluvius Imewo 2026 en heffingen zoals op een gangbare tariefkaart 2026,
# nagekeken met echte facturen. Andere netbeheerders en leveranciers verschillen: altijd
# vergelijken met je eigen factuur.
VOORBEELD_NET = {
    "distributie": 5.22864, "groenestroom": 1.074, "wkk": 0.392, "energiebijdrage": 0.19261,
    "accijns": 4.748, "capaciteit": 54.20, "databeheer": 17.85, "abonnement": 47.17, "btw": 6,
}

SOORTEN = ("enkel", "dagnacht", "variabel", "dynamisch", "blok")


# ---------- contract uit de config ----------

def contract(cfg):
    f = lambda k, d=0.0: ec.cfg_float(cfg, "tarief", k, d)
    dag = f("dag_eur_kwh")
    nacht = f("nacht_eur_kwh", dag)
    soort = ec.cfg_str(cfg, "tarief", "soort", "")
    if soort not in SOORTEN:
        soort = "enkel" if dag == nacht else "dagnacht"
    btw = f("btw_procent", 6.0)
    incl = ec.cfg_str(cfg, "tarief", "bedragen", "excl") == "incl"
    deel = (1 + btw / 100) if incl else 1.0          # ingegeven incl. btw -> intern excl. btw
    c = {
        "soort": soort, "btw": btw, "incl": incl,
        "dag": dag / deel, "nacht": nacht / deel,
        "dag_start": ec.cfg_int(cfg, "tarief", "dag_start_uur", 7),
        "dag_einde": ec.cfg_int(cfg, "tarief", "dag_einde_uur", 22),
        "weekend_nacht": ec.cfg_bool(cfg, "tarief", "weekend_als_nacht", True),
        "factor": f("formule_factor", 1.0), "toeslag": f("formule_toeslag_eur_mwh") / deel,
        "blok_kwh": f("blok_kwh_jaar"), "blok_prijs": f("blok_eur_kwh") / deel,
        "blok_verdeling": ec.cfg_str(cfg, "tarief", "blok_verdeling", "spp"),
        "blok_door": ec.cfg_bool(cfg, "tarief", "blok_doorschuiven", False),
        "inj_soort": ec.cfg_str(cfg, "tarief", "injectie_soort", "vast"),
        "inj_vast": f("injectie_eur_kwh"),
        "inj_factor": f("injectie_factor", 1.0), "inj_toeslag": f("injectie_toeslag_eur_mwh"),
        "inj_min": ec.cfg_str(cfg, "tarief", "injectie_min_eur_kwh", ""),
        "net_kwh": sum(f(k) for k in ("distributie_eur_kwh", "groenestroom_eur_kwh", "wkk_eur_kwh",
                                      "energiebijdrage_eur_kwh", "accijns_eur_kwh")) / deel,
        "capaciteit": f("capaciteit_eur_kw_jaar") / deel,
        "vast_jaar": (f("databeheer_eur_jaar") + f("vast_eur_jaar")) / deel,
        "min_piek_kw": ec.cfg_float(cfg, "installatie", "minimum_piek_kw", 2.5),
    }
    try:
        c["inj_min"] = float(c["inj_min"].replace(",", ".")) if c["inj_min"] else None
    except ValueError:
        c["inj_min"] = None
    return c


def heeft_marktprijzen_nodig(c):
    return c["soort"] in ("variabel", "dynamisch", "blok") or c["inj_soort"] in ("formule", "dynamisch")


def is_dag(dt_lokaal, c):
    if c["weekend_nacht"] and dt_lokaal.weekday() >= 5:
        return False
    return c["dag_start"] <= dt_lokaal.hour < c["dag_einde"]


# ---------- marktprijzen en maandindex ----------

class Prijzen:
    """Marktprijzen (EUR/kWh) uit de tabel prijzen, opvraagbaar per tijdstip."""

    def __init__(self, db, van, tot):
        rows = db.execute("SELECT start_utc, eur_kwh FROM prijzen WHERE start_utc>=? AND start_utc<? ORDER BY start_utc",
                          (ec.iso(van - timedelta(hours=1)), ec.iso(tot))).fetchall()
        self.t = [ec.parse_iso(r[0]) for r in rows]
        self.p = [r[1] for r in rows]

    def op(self, t):
        i = bisect.bisect_right(self.t, t) - 1
        if i < 0 or t - self.t[i] > timedelta(hours=1):
            return None
        return self.p[i]


def maand_grenzen(jaar, maand, tz):
    a = datetime(jaar, maand, 1, tzinfo=tz)
    b = datetime(jaar + (maand == 12), maand % 12 + 1, 1, tzinfo=tz)
    return a.astimezone(timezone.utc), b.astimezone(timezone.utc)


_INDEX_CACHE = {}


def maandindex(db, jaar, maand, soort, tz, officieel=True):
    """(EUR/MWh, 'officieel'|'schatting'|None) voor soort 'afname' of 'injectie'.
    officieel=False geeft altijd de schatting (om te vergelijken)."""
    sleutel = f"{jaar:04d}-{maand:02d}"
    if officieel:
        r = db.execute("SELECT eur_mwh FROM maandindex WHERE maand=? AND soort=?", (sleutel, soort)).fetchone()
        if r and r[0] is not None:
            return r[0], "officieel"
    van, tot = maand_grenzen(jaar, maand, tz)
    nu = ec.utc_nu()
    cache_sleutel = (sleutel, soort)
    oud = _INDEX_CACHE.get(cache_sleutel)
    if oud and (tot < nu or (nu - oud[2]).total_seconds() < 900):
        return oud[0], oud[1]
    prijzen = Prijzen(db, van, tot)
    kol = "import_kwh" if soort == "afname" else "export_kwh"
    kw = db.execute(f"SELECT einde_utc, {kol} FROM kwartieren WHERE einde_utc>? AND einde_utc<=? AND {kol} IS NOT NULL",
                    (ec.iso(van), ec.iso(tot))).fetchall()
    som = gewicht = 0.0
    for einde, kwh in kw:
        p = prijzen.op(ec.parse_iso(einde) - timedelta(minutes=15))
        if p is not None and kwh and kwh > 0:
            som += p * kwh
            gewicht += kwh
    if gewicht > 0.5:
        waarde = som / gewicht * 1000
    elif prijzen.p:
        waarde = sum(prijzen.p) / len(prijzen.p) * 1000
    else:
        waarde = None
    uit = (round(waarde, 3) if waarde is not None else None, "schatting" if waarde is not None else None)
    _INDEX_CACHE[cache_sleutel] = (uit[0], uit[1], nu)
    return uit


def zet_maandindex(db, maand, soort, waarde):
    if waarde is None:
        db.execute("DELETE FROM maandindex WHERE maand=? AND soort=?", (maand, soort))
    else:
        db.execute("INSERT OR REPLACE INTO maandindex(maand, soort, eur_mwh) VALUES(?,?,?)", (maand, soort, waarde))
    db.commit()
    _INDEX_CACHE.clear()


# ---------- blok ----------

def blok_maand(c, jaar, maand):
    if c["blok_kwh"] <= 0:
        return 0.0
    if c["blok_verdeling"] == "spp":
        return c["blok_kwh"] * SPP_2026[maand - 1] / 1000.0
    dagen_jaar = 366 if calendar.isleap(jaar) else 365
    return c["blok_kwh"] * calendar.monthrange(jaar, maand)[1] / dagen_jaar


def _afname_tussen(db, van, tot):
    a = ec._tellerstand_op(db, van)
    if a is None or a[0] is None:
        a = db.execute("SELECT import_kwh FROM metingen WHERE ts>=? ORDER BY ts LIMIT 1", (ec.iso(van),)).fetchone()
    b = ec._tellerstand_op(db, tot)
    if not a or not b or a[0] is None or b[0] is None:
        return 0.0
    d = b[0] - a[0]
    return d if 0 <= d < 100000 else 0.0


def blok_rest_bij_start(db, c, datum, tz):
    """Hoeveel blok-kWh er nog over is bij het begin van deze dag."""
    if c["soort"] != "blok" or c["blok_kwh"] <= 0:
        return 0.0
    dag_start = datetime(datum.year, datum.month, datum.day, tzinfo=tz).astimezone(timezone.utc)
    if c["blok_door"]:
        toegekend = sum(blok_maand(c, datum.year, m) for m in range(1, datum.month + 1))
        jaar_start = datetime(datum.year, 1, 1, tzinfo=tz).astimezone(timezone.utc)
        return max(0.0, toegekend - _afname_tussen(db, jaar_start, dag_start))
    maand_start, _ = maand_grenzen(datum.year, datum.month, tz)
    return max(0.0, blok_maand(c, datum.year, datum.month) - _afname_tussen(db, maand_start, dag_start))


# ---------- capaciteit ----------

def gemiddelde_piek_kw(db, datum, tz, c):
    """Gemiddelde van de maandpieken van de laatste 12 maanden met metingen (elk minstens het minimum)."""
    pieken = []
    j, m = datum.year, datum.month
    for _ in range(12):
        van, tot = maand_grenzen(j, m, tz)
        heeft = db.execute("SELECT 1 FROM kwartieren WHERE einde_utc>? AND einde_utc<=? AND compleet=1 LIMIT 1",
                           (ec.iso(van), ec.iso(tot))).fetchone()
        if heeft:
            pieken.append(max(c["min_piek_kw"], ec.maandpiek(db, tz, j, m) / 1000.0))
        j, m = (j - 1, 12) if m == 1 else (j, m - 1)
    if not pieken:
        return c["min_piek_kw"], 0
    return sum(pieken) / len(pieken), len(pieken)


# ---------- de kosten van één dag ----------

def kosten_dag(db, datum, tz, cfg):
    """Kosten van een lokale kalenderdag, met regels zoals op een factuur."""
    c = contract(cfg)
    start, einde = ec.dag_grenzen_utc(datum, tz)
    rows = db.execute("SELECT ts, import_kwh, export_kwh FROM metingen WHERE ts>=? AND ts<? ORDER BY ts",
                      (ec.iso(start), ec.iso(einde))).fetchall()
    vorige = db.execute("SELECT import_kwh, export_kwh FROM metingen WHERE ts<? ORDER BY ts DESC LIMIT 1",
                        (ec.iso(start),)).fetchone()
    prijzen = Prijzen(db, start, einde) if c["soort"] == "dynamisch" or c["inj_soort"] == "dynamisch" else None
    idx_af = idx_inj = (None, None)
    if c["soort"] in ("variabel", "blok"):
        idx_af = maandindex(db, datum.year, datum.month, "afname", tz)
    if c["inj_soort"] == "formule":
        idx_inj = maandindex(db, datum.year, datum.month, "injectie", tz)
    blok_rest = blok_rest_bij_start(db, c, datum, tz)

    kwh = {"dag": 0.0, "nacht": 0.0, "blok": 0.0, "var": 0.0, "inj": 0.0}
    eur = {"var": 0.0, "inj": 0.0}
    zonder_prijs = 0.0
    for ts, i, e in rows:
        if vorige and vorige[0] is not None and i is not None:
            di, de = i - vorige[0], e - vorige[1]
            t = ec.parse_iso(ts)
            if 0 <= di < 50:
                if c["soort"] in ("enkel", "dagnacht"):
                    kwh["dag" if (c["soort"] == "enkel" or is_dag(t.astimezone(tz), c)) else "nacht"] += di
                else:
                    deel_blok = min(di, blok_rest) if c["soort"] == "blok" else 0.0
                    blok_rest -= deel_blok
                    kwh["blok"] += deel_blok
                    rest = di - deel_blok
                    if rest > 0:
                        idx = (prijzen.op(t) * 1000 if prijzen and prijzen.op(t) is not None else None) \
                            if c["soort"] == "dynamisch" else idx_af[0]
                        if idx is None:
                            zonder_prijs += rest
                        else:
                            kwh["var"] += rest
                            eur["var"] += rest * (c["factor"] * idx + c["toeslag"]) / 1000
            if 0 <= de < 50 and de > 0:
                kwh["inj"] += de
                if c["inj_soort"] == "vast":
                    eur["inj"] += de * c["inj_vast"]
                else:
                    idx = (prijzen.op(t) * 1000 if prijzen and prijzen.op(t) is not None else None) \
                        if c["inj_soort"] == "dynamisch" else idx_inj[0]
                    if idx is not None:
                        p = (c["inj_factor"] * idx + c["inj_toeslag"]) / 1000
                        if c["inj_min"] is not None:
                            p = max(p, c["inj_min"])
                        eur["inj"] += de * p
        vorige = (i, e) if i is not None else vorige

    regels, waarschuwingen = [], []
    def regel(tekst, hoeveel, prijs, bedrag):
        regels.append({"tekst": tekst, "hoeveel": hoeveel, "prijs": prijs, "eur": round(bedrag, 4)})

    afname = kwh["dag"] + kwh["nacht"] + kwh["blok"] + kwh["var"] + zonder_prijs
    if c["soort"] == "enkel":
        regel("Energie", f"{ec.komma(kwh['dag'], 2)} kWh", f"{ec.komma(c['dag'] * 100, 3)} c€", kwh["dag"] * c["dag"])
    elif c["soort"] == "dagnacht":
        regel("Energie dagtarief", f"{ec.komma(kwh['dag'], 2)} kWh", f"{ec.komma(c['dag'] * 100, 3)} c€", kwh["dag"] * c["dag"])
        regel("Energie nachttarief", f"{ec.komma(kwh['nacht'], 2)} kWh", f"{ec.komma(c['nacht'] * 100, 3)} c€", kwh["nacht"] * c["nacht"])
    else:
        if c["soort"] == "blok":
            regel("Energie vast blok", f"{ec.komma(kwh['blok'], 2)} kWh", f"{ec.komma(c['blok_prijs'] * 100, 3)} c€",
                  kwh["blok"] * c["blok_prijs"])
        if c["soort"] == "dynamisch":
            gem = eur["var"] / kwh["var"] * 100 if kwh["var"] else None
            regel("Energie dynamisch (per kwartier)", f"{ec.komma(kwh['var'], 2)} kWh",
                  f"gem. {ec.komma(gem, 3)} c€" if gem is not None else "-", eur["var"])
        elif kwh["var"] or c["soort"] == "variabel":
            p = (c["factor"] * idx_af[0] + c["toeslag"]) / 1000 if idx_af[0] is not None else None
            regel("Energie variabel" + (" (boven het blok)" if c["soort"] == "blok" else ""),
                  f"{ec.komma(kwh['var'], 2)} kWh", f"{ec.komma(p * 100, 3)} c€" if p is not None else "-", eur["var"])
            if idx_af[1] == "schatting":
                waarschuwingen.append(f"Maandindex afname {datum.strftime('%m/%Y')} is een schatting "
                                      f"({ec.komma(idx_af[0], 2)} €/MWh); vul de officiële waarde in zodra je leverancier ze publiceert.")
        if zonder_prijs > 0.001:
            waarschuwingen.append(f"{ec.komma(zonder_prijs, 2)} kWh zonder marktprijs: niet meegeteld. "
                                  "Zet de marktprijzen aan (Instellingen -> Marktprijzen).")
    energie = sum(r["eur"] for r in regels)
    if c["net_kwh"]:
        regel("Netkosten en heffingen", f"{ec.komma(afname, 2)} kWh", f"{ec.komma(c['net_kwh'] * 100, 3)} c€", afname * c["net_kwh"])
    if c["capaciteit"]:
        piek, n = gemiddelde_piek_kw(db, datum, tz, c)
        regel(f"Capaciteitstarief (gem. piek {n} maand{'en' if n != 1 else ''})", f"{ec.komma(piek, 2)} kW",
              f"{ec.komma(c['capaciteit'], 2)} €/kW/jaar", piek * c["capaciteit"] / 365)
    if c["vast_jaar"]:
        regel("Vaste kosten (dagdeel)", "1 dag", f"{ec.komma(c['vast_jaar'], 2)} €/jaar", c["vast_jaar"] / 365)
    subtotaal = sum(r["eur"] for r in regels)
    btw_eur = subtotaal * c["btw"] / 100
    regel(f"Btw {ec.komma(c['btw'], 0)} %", "", "", btw_eur)
    inj = eur["inj"]
    if kwh["inj"] or c["inj_vast"] or c["inj_soort"] != "vast":
        gem = inj / kwh["inj"] * 100 if kwh["inj"] else None
        regel("Teruglevering (vergoeding)", f"{ec.komma(kwh['inj'], 2)} kWh",
              f"{ec.komma(gem, 3)} c€" if gem is not None else "-", -inj)
        if c["inj_soort"] == "formule" and idx_inj[1] == "schatting":
            waarschuwingen.append(f"Maandindex injectie {datum.strftime('%m/%Y')} is een schatting ({ec.komma(idx_inj[0], 2)} €/MWh).")
    totaal = subtotaal + btw_eur - inj
    return {
        "regels": regels, "waarschuwingen": waarschuwingen,
        "energie_eur": round(energie, 4), "subtotaal_eur": round(subtotaal, 4), "btw_eur": round(btw_eur, 4),
        "injectie_eur": round(inj, 4), "totaal_eur": round(totaal, 2),
        "afname_kwh": round(afname, 3), "injectie_kwh": round(kwh["inj"], 3),
        "blok_rest_kwh": round(max(blok_rest, 0), 2) if c["soort"] == "blok" else None,
    }


def kosten_periode(db, van_datum, tot_datum, tz, cfg):
    """Som van de dagen van van_datum t/m tot_datum."""
    tot, d = 0.0, van_datum
    while d <= tot_datum:
        tot += kosten_dag(db, d, tz, cfg)["totaal_eur"]
        d += timedelta(days=1)
    return round(tot, 2)


def tabel_html(k):
    """De regels als HTML-tabel (dashboard en rapport)."""
    h = ["<table><tr><th>Onderdeel</th><th>Hoeveelheid</th><th>Prijs (excl. btw)</th><th>Bedrag</th></tr>"]
    for r in k["regels"]:
        h.append(f"<tr><td>{r['tekst']}</td><td>{r['hoeveel']}</td><td>{r['prijs']}</td><td>{ec.euro(r['eur'])}</td></tr>")
    h.append(f"<tr><td><strong>Totaal</strong></td><td></td><td></td><td><strong>{ec.euro(k['totaal_eur'])}</strong></td></tr></table>")
    for w in k["waarschuwingen"]:
        h.append(f"<p class='uitleg'>⚠ {w}</p>")
    return "".join(h)


# ---------- gas en water ----------

def gaswater(cfg):
    """Tarief voor gas en water uit [gaswater]; bedragen in EUR."""
    f = lambda k, d=0.0: ec.cfg_float(cfg, "gaswater", k, d)
    return {
        "gas_incl": ec.cfg_str(cfg, "gaswater", "gas_bedragen", "excl") == "incl",
        "gas_btw": f("gas_btw_procent", 6.0),
        "gas_kwh_m3": f("gas_kwh_m3", 11.2),
        "gas_energie": f("gas_energie_eur_kwh"),
        "gas_net": f("gas_net_eur_kwh"),
        "gas_vast": f("gas_vast_eur_jaar"),
        "water_m3": f("water_eur_m3"),
        "water_vast": f("water_vast_eur_jaar"),
    }


def gas_ingevuld(t):
    return bool(t["gas_energie"] or t["gas_net"])


def water_ingevuld(t):
    return bool(t["water_m3"])


def kosten_gas(m3, dagen, t):
    """Kosten (EUR, incl. btw) van m3 gas plus 'dagen' vaste kosten. None als het tarief niet ingevuld is."""
    if m3 is None or not gas_ingevuld(t):
        return None
    k = m3 * t["gas_kwh_m3"] * (t["gas_energie"] + t["gas_net"]) + t["gas_vast"] * dagen / 365
    return round(k if t["gas_incl"] else k * (1 + t["gas_btw"] / 100), 2)


def kosten_water(m3, dagen, t):
    """Kosten (EUR, incl. btw) van m3 water plus 'dagen' vaste kosten. None als het tarief niet ingevuld is."""
    if m3 is None or not water_ingevuld(t):
        return None
    return round(m3 * t["water_m3"] + t["water_vast"] * dagen / 365, 2)
