#!/usr/bin/env python3
"""ems_rapport - maakt het dagelijkse rapport (HTML), een back-up van de metingen, ruimt oude ruwe data op
en kan het rapport per mail sturen (als [mail] aan = ja in de config).

Conclusie staat bovenaan in één zin ("OK" of "AANDACHT" met de reden).
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

import ems_common as ec
import ems_kosten


def beoordeel(totalen, kwartieren, spanning_min, budget_w, verwacht, interval):
    redenen = []
    if totalen["metingen"] < verwacht * 0.95:
        redenen.append(f"data onvolledig ({totalen['metingen']} van ca. {verwacht} metingen)")
    piek = max((k[1] for k in kwartieren if k[1] is not None), default=None)
    if piek is not None and piek > budget_w:
        redenen.append(f"kwartierpiek {ec.kw_w(piek)} boven het budget van {ec.kw_w(budget_w)}")
    if spanning_min > 0:
        redenen.append(f"netspanning {ec.komma(spanning_min)} min buiten 207–253 V")
    return ("OK", "Geen bijzonderheden.") if not redenen else ("AANDACHT", "; ".join(redenen) + ".")


def maak_rapport(cfgpad, datum, backup_melding=None):
    cfg = ec.laad_config(cfgpad)
    tz = ec.tijdzone(cfg)
    db_pad = ec.db_pad(cfgpad, cfg)
    rapport_map = ec.pad_in_project(cfgpad, ec.cfg_str(cfg, "rapport", "map", "rapporten"))
    interval = max(ec.cfg_int(cfg, "meter", "interval_seconden", 10), 1)
    db = ec.open_db(db_pad)

    totalen = ec.dag_totalen(db, datum, tz, cfg)
    start, einde = ec.dag_grenzen_utc(datum, tz)
    kw = db.execute(
        "SELECT einde_utc, gemiddeld_w, import_kwh, export_kwh FROM kwartieren"
        " WHERE einde_utc>? AND einde_utc<=? AND compleet=1 ORDER BY gemiddeld_w DESC LIMIT 5",
        (ec.iso(start), ec.iso(einde))).fetchall()
    rows = db.execute(
        "SELECT ts, vermogen_w, l1_v, l2_v, l3_v FROM metingen WHERE ts>=? AND ts<? ORDER BY ts",
        (ec.iso(start), ec.iso(einde))).fetchall()
    stap = max(1, len(rows) // 800)
    punten = [(ec.parse_iso(r[0]), r[1]) for r in rows[::stap] if r[1] is not None]
    buiten = sum(1 for r in rows if any(v is not None and not (207 <= v <= 253) for v in r[2:5]))
    spanning_min = buiten * interval / 60
    prijzen = db.execute(
        "SELECT COUNT(*), AVG(eur_kwh), MIN(eur_kwh), MAX(eur_kwh) FROM prijzen"
        " WHERE start_utc>=? AND start_utc<?", (ec.iso(start), ec.iso(einde))).fetchone()

    minimum = ec.cfg_float(cfg, "installatie", "minimum_piek_kw", 2.5) * 1000
    piek_maand = ec.maandpiek(db, tz, datum.year, datum.month)
    budget = max(minimum, piek_maand)
    verwacht = round((einde - start).total_seconds() / interval)
    oordeel, reden = beoordeel(totalen, kw, spanning_min, budget, verwacht, interval)

    datum_nl = datum.strftime("%d/%m/%Y")
    h = ["<html><head><meta charset='utf-8'><title>HEMS dagrapport " + datum_nl + "</title>",
         "<style>body{font-family:sans-serif;max-width:900px;margin:0 auto;padding:14px;color:#222}"
         "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:4px 8px;text-align:right}"
         "th:first-child,td:first-child{text-align:left}.ok{color:#1a7a1a}.fout{color:#b00020}"
         "svg{background:#fff;border:1px solid #ddd;max-width:100%}</style></head><body>"]
    h.append(f"<h1>Dagrapport {datum_nl}</h1>")
    if ec.demo_actief(cfg):
        h.append("<p class='fout'><strong>DEMO-MODUS: gesimuleerde metingen, niet die van je meter.</strong></p>")
    klasse = "ok" if oordeel == "OK" else "fout"
    h.append(f"<p><strong class='{klasse}'>Oordeel: {oordeel}</strong> — {reden}</p>")
    h.append(f"<h2>Verbruik</h2><p>Afname {ec.komma(totalen['import_dag_kwh'] + totalen['import_nacht_kwh'], 2)} kWh "
             f"(dagtarief {ec.komma(totalen['import_dag_kwh'], 2)}, nachttarief {ec.komma(totalen['import_nacht_kwh'], 2)}), "
             f"teruglevering {ec.komma(totalen['export_kwh'], 2)} kWh.</p>")
    h.append("<h2>Kosten (zoals op een factuur)</h2>" + ems_kosten.tabel_html(totalen["kosten"]))
    if kw:
        h.append("<h2>Top-5 kwartierpieken</h2><table>"
                 "<tr><th>Kwartier eindigt</th><th>Gemiddeld vermogen</th><th>Afname</th><th>Teruglevering</th></tr>")
        for einde_utc, gem, imp, exp in kw:
            lbl = ec.parse_iso(einde_utc).astimezone(tz).strftime("%d/%m %H:%M")
            h.append(f"<tr><td>{lbl}</td><td>{ec.kw_w(gem)}</td><td>{ec.komma(imp,3)} kWh</td><td>{ec.komma(exp,3)} kWh</td></tr>")
        h.append("</table>")
    h.append(f"<p>Maandpiek tot nu: <strong>{ec.kw_w(piek_maand)}</strong> "
             f"(budget: {ec.kw_w(budget)}; in Vlaanderen telt een piek onder {ec.komma(minimum/1000,1)} kW als {ec.komma(minimum/1000,1)} kW).</p>")
    if prijzen and prijzen[0]:
        h.append(f"<h2>Marktprijzen</h2><p>{prijzen[0]} prijspunten; gemiddeld {ec.komma(prijzen[1]*100,2)} c€/kWh "
                 f"(min {ec.komma(prijzen[2]*100,2)}, max {ec.komma(prijzen[3]*100,2)}), kale marktprijs zonder netkosten en heffingen.</p>")
    h.append("<h2>Grafiek</h2>")
    h.append(ec.svg_grafiek(punten, f"Vermogen op {datum_nl}", tz=tz) if punten else "<p>Geen metingen.</p>")
    if backup_melding:
        fout = "LET OP" in backup_melding or backup_melding.startswith("back-up mislukt")
        h.append(f"<p class='{'fout' if fout else 'ok'}'>Back-up van de metingen: {__import__('html').escape(backup_melding)}.</p>")
    h.append(f"<p style='color:#777;font-size:.85em'>Volledigheid: {totalen['metingen']} van ca. {verwacht} metingen. "
             "Kosten volgens je contract in Instellingen; vergelijk af en toe met je echte factuur. "
             "Dit systeem meet enkel en stuurt niets.</p></body></html>")
    html = "".join(h)

    import os
    os.makedirs(rapport_map, exist_ok=True)
    pad = os.path.join(rapport_map, datum.isoformat() + ".html")
    with open(pad, "w", encoding="utf-8") as f:
        f.write(html)

    # oude ruwe metingen opruimen (kwartieren en prijzen blijven)
    dagen = ec.cfg_int(cfg, "opslag", "bewaar_ruwe_dagen", 730)
    grens = ec.iso(ec.utc_nu() - timedelta(days=dagen))
    with db:
        weg = db.execute("DELETE FROM metingen WHERE ts<?", (grens,)).rowcount
    db.close()
    print(f"rapport {pad} geschreven ({oordeel}); {weg} oude ruwe metingen opgeruimd")
    return html, oordeel, datum_nl, pad


def verstuur_mail(cfg, cfgpad, html, onderwerp):
    if not ec.cfg_bool(cfg, "mail", "aan", False):
        return
    try:
        ec.stuur_mail(cfg, cfgpad, onderwerp, "Het dagrapport staat in de bijlage (HTML).", html)
        print("rapport per mail verstuurd")
    except Exception as e:
        print(f"mail versturen mislukt: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="HEMS-dagrapport")
    ap.add_argument("--config", required=True, help="pad naar ems.ini")
    ap.add_argument("--datum", help="YYYY-MM-DD (standaard: gisteren, lokale tijd)")
    args = ap.parse_args()
    cfg = ec.laad_config(args.config)
    tz = ec.tijdzone(cfg)
    if args.datum:
        datum = datetime.strptime(args.datum, "%Y-%m-%d").date()
    else:
        datum = (ec.utc_nu().astimezone(tz) - timedelta(days=1)).date()
    try:
        backup = ec.backup_db(args.config, cfg)
    except Exception as e:
        backup = f"back-up mislukt: {e}"
    print(backup, file=sys.stderr if "mislukt" in backup else sys.stdout)
    html, oordeel, datum_nl, _ = maak_rapport(args.config, datum, backup)
    verstuur_mail(cfg, args.config, html, f"HEMS dagrapport {datum_nl}: {oordeel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
