#!/usr/bin/env python3
"""ems_meterd - leest de digitale meter uit via de lokale API en logt naar SQLite.

Ondersteunt de HomeWizard P1 Meter (lokale API v1: http://<ip>/api/v1/data) en
andere dongles die hetzelfde JSON-formaat teruggeven. In demo-modus ([meter] demo in ems.ini, of --demo) draait hij met
gesimuleerde metingen, bv. om het dashboard te bekijken zonder meter.

Dit programma leest alleen; het schrijft nooit naar een toestel.
"""
import argparse
import json
import random
import signal
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import ems_common as ec

_stop = False


def _sig(signum, frame):
    global _stop
    _stop = True


def lees_meter(url, timeout=5):
    verzoek = urllib.request.Request(url, headers={"User-Agent": "hems-meting/1.0"})
    with urllib.request.urlopen(verzoek, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def hw_naar_rij(d):
    """Zet het JSON-antwoord van een HomeWizard-compatibele dongle om naar een databaserij."""
    def g(k):
        v = d.get(k)
        return float(v) if v is not None else None
    imp = (g("total_power_import_t1_kwh") or 0) + (g("total_power_import_t2_kwh") or 0)
    exp = (g("total_power_export_t1_kwh") or 0) + (g("total_power_export_t2_kwh") or 0)
    piek = d.get("montly_power_peak_w", d.get("monthly_power_peak_w"))
    # gas- en watermeter hangen aan de digitale meter: HomeWizard geeft ze in de lijst "external"
    # (gas ook nog als total_gas_m3). Bij meerdere meters van dezelfde soort: de eerste.
    extern = {}
    for e in d.get("external") or []:
        if isinstance(e, dict) and e.get("type") and e.get("value") is not None and e.get("unit") in ("m3", "m³", None):
            extern.setdefault(e["type"], float(e["value"]))
    gas = g("total_gas_m3")
    if gas is None:
        gas = extern.get("gas_meter")
    return (
        ec.iso(ec.utc_nu()), round(imp, 3), round(exp, 3), g("active_power_w"),
        g("active_power_l1_w"), g("active_power_l2_w"), g("active_power_l3_w"),
        g("active_current_l1_a"), g("active_current_l2_a"), g("active_current_l3_a"),
        g("active_voltage_l1_v"), g("active_voltage_l2_v"), g("active_voltage_l3_v"),
        gas, extern.get("water_meter"),
        g("active_power_average_w"), float(piek) if piek is not None else None,
    )


class DemoMeter:
    """Simuleert een meter: basislast, koelkast, kookpieken en zon overdag."""

    def __init__(self):
        self.import_kwh = 18345.123
        self.export_kwh = 4567.890
        self.t = ec.utc_nu()

    def lees(self):
        nu = ec.utc_nu()
        dt = max((nu - self.t).total_seconds(), 0.5)
        self.t = nu
        uur = (nu + timedelta(hours=1)).hour + nu.minute / 60      # ongeveer Belgische tijd
        vermogen = 250 + 120 * random.random()                      # basislast
        if random.random() < 0.05:
            vermogen += 900 + 600 * random.random()                 # koffiezet/waterkoker
        if 7 <= uur <= 8.5 or 18 <= uur <= 20:
            vermogen += 800 * random.random()                       # koken
        if 9 <= uur <= 17:
            zon = max(0, 2200 * (1 - abs(uur - 13) / 4))            # gesimuleerde zon
            vermogen -= zon * (0.75 + 0.25 * random.random())
        kw = vermogen / 1000 * dt / 3600
        if vermogen >= 0:
            self.import_kwh += kw
        else:
            self.export_kwh -= kw
        return {
            "total_power_import_t1_kwh": self.import_kwh * 0.6,
            "total_power_import_t2_kwh": self.import_kwh * 0.4,
            "total_power_export_t1_kwh": self.export_kwh * 0.6,
            "total_power_export_t2_kwh": self.export_kwh * 0.4,
            "active_power_w": vermogen,
            "active_power_l1_w": vermogen / 3, "active_power_l2_w": vermogen / 3,
            "active_power_l3_w": vermogen / 3,
            "active_current_l1_a": abs(vermogen) / 3 / 230, "active_current_l2_a": abs(vermogen) / 3 / 230,
            "active_current_l3_a": abs(vermogen) / 3 / 230,
            "active_voltage_l1_v": 231.0, "active_voltage_l2_v": 230.2, "active_voltage_l3_v": 229.8,
            "active_power_average_w": None, "montly_power_peak_w": None,
        }


INSERT = ("INSERT OR REPLACE INTO metingen(ts, import_kwh, export_kwh, vermogen_w,"
          " l1_w, l2_w, l3_w, l1_a, l2_a, l3_a, l1_v, l2_v, l3_v, gas_m3, water_m3,"
          " meter_kwartier_w, meter_maandpiek_w) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def eenmalig(url):
    """Lees één meting en toon ze (gebruikt door de installer als eerste test)."""
    d = lees_meter(url)
    rij = hw_naar_rij(d)
    print("Meting van", rij[0], "(UTC):")
    print(f"  Vermogen nu       : {ec.kw_w(rij[3])} ({'afname' if (rij[3] or 0) >= 0 else 'teruglevering'})")
    print(f"  Teller afname     : {ec.komma(rij[1], 3)} kWh")
    print(f"  Teller injectie   : {ec.komma(rij[2], 3)} kWh")
    if rij[4] is not None:
        print(f"  Vermogen per fase : L1 {ec.kw_w(rij[4])} | L2 {ec.kw_w(rij[5])} | L3 {ec.kw_w(rij[6])}")
    if rij[10] is not None:
        print(f"  Spanning per fase : L1 {ec.komma(rij[10])} V | L2 {ec.komma(rij[11])} V | L3 {ec.komma(rij[12])} V")
    if rij[15] is not None:
        # Het lopende kwartier begint op :00, :15, :30, :45; vlak na het begin zegt het gemiddelde nog weinig.
        try:
            t = datetime.fromisoformat(str(rij[0]).replace("Z", "+00:00"))
            bezig = (t.minute % 15) * 60 + t.second
        except ValueError:
            bezig = None
        if bezig is not None and bezig < 120:
            print(f"  Kwartiergemiddelde: {ec.kw_w(rij[15])} (het kwartier is pas {bezig} s bezig: nog niet zinvol)")
        elif bezig is not None:
            print(f"  Kwartiergemiddelde: {ec.kw_w(rij[15])} (lopend kwartier, {bezig // 60} min bezig)")
        else:
            print(f"  Kwartiergemiddelde: {ec.kw_w(rij[15])} (lopend kwartier, door de meter berekend)")
    if rij[16] is not None:
        print(f"  Maandpiek         : {ec.kw_w(rij[16])} (door de meter berekend)")
    ontbrekend = []
    if rij[4] is None:
        ontbrekend.append("vermogen per fase")
    if rij[7] is None:
        ontbrekend.append("stroom per fase")
    if rij[15] is None:
        ontbrekend.append("kwartiergemiddelde en maandpiek (het systeem berekent ze dan zelf)")
    if ontbrekend:
        print("  Niet aanwezig in deze meter:", "; ".join(ontbrekend))


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)     # meldingen meteen in het logboek (journal)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="HEMS-meterdaemon (alleen lezen)")
    ap.add_argument("--config", required=True, help="pad naar ems.ini")
    ap.add_argument("--demo", action="store_true", help="gesimuleerde metingen forceren (standaard volgt [meter] demo in ems.ini)")
    ap.add_argument("--eenmalig", action="store_true", help="één meting lezen, tonen en stoppen")
    args = ap.parse_args()
    cfg = ec.laad_config(args.config)
    is_demo = ec.demo_actief(cfg, args.demo)

    if args.eenmalig:
        if is_demo:
            rij = hw_naar_rij(DemoMeter().lees())
            print("DEMO-meting (geen echte meter):")
            print(f"  Vermogen: {ec.kw_w(rij[3])}")
            return 0
        url = ec.cfg_str(cfg, "meter", "url")
        if not url:
            print("FOUT: geen meter-url in de configuratie (sleutel [meter] url).", file=sys.stderr)
            return 1
        try:
            eenmalig(url)
            return 0
        except Exception as e:
            print(f"FOUT: de meter antwoordt niet op {url}: {e}", file=sys.stderr)
            return 1

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    # Toestand die uit de config volgt; wordt opnieuw ingelezen zodra ems.ini wijzigt
    # (bv. via de pagina Instellingen van het dashboard), zonder herstart.
    st = {"mtime": ec.config_mtime(args.config), "db": None, "db_pad": None,
          "demo": None, "url": "", "interval": 10, "wacht_gemeld": False}

    def pas_toe(cfg):
        is_demo = ec.demo_actief(cfg, args.demo)
        st["url"] = ec.cfg_str(cfg, "meter", "url")
        st["interval"] = max(ec.cfg_int(cfg, "meter", "interval_seconden", 10), 1)
        if is_demo and st["demo"] is None:
            st["demo"] = DemoMeter()
        elif not is_demo:
            st["demo"] = None
        nieuw_pad = ec.db_pad(args.config, cfg, args.demo)
        if nieuw_pad != st["db_pad"]:
            if st["db"] is not None:
                st["db"].close()
            st["db"] = ec.open_db(nieuw_pad)
            st["db_pad"] = nieuw_pad
        bron = "demo" if st["demo"] else (st["url"] or "geen meter-url")
        print(f"{ec.iso(ec.utc_nu())} meten: {bron}, elke {st['interval']} s -> {st['db_pad']}")

    pas_toe(cfg)
    mislukt = 0
    while not _stop:
        t0 = time.monotonic()
        m = ec.config_mtime(args.config)
        if m != st["mtime"]:
            st["mtime"] = m
            try:
                cfg = ec.laad_config(args.config)
                print(f"{ec.iso(ec.utc_nu())} configuratie gewijzigd: opnieuw ingelezen")
                pas_toe(cfg)
                mislukt = 0
                st["wacht_gemeld"] = False
            except Exception as e:
                print(f"{ec.iso(ec.utc_nu())} waarschuwing: gewijzigde configuratie onleesbaar, oude blijft: {e}", file=sys.stderr)
        demo, url, db, interval = st["demo"], st["url"], st["db"], st["interval"]
        if not demo and not url:
            if not st["wacht_gemeld"]:
                print("geen meter-url en demo = nee: wacht tot er een meter is ingesteld "
                      "(dashboard -> Instellingen, of [meter] url in ems.ini)", file=sys.stderr)
                st["wacht_gemeld"] = True
        else:
            try:
                d = demo.lees() if demo else lees_meter(url)
                rij = hw_naar_rij(d)
                try:
                    db.execute(INSERT, rij)
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                if mislukt >= 3:
                    print(f"{ec.iso(ec.utc_nu())} meter weer bereikbaar na {mislukt} mislukte pogingen")
                mislukt = 0
                ec.sluit_kwartieren(db)
            except Exception as e:
                mislukt += 1
                if mislukt in (1, 3, 10) or mislukt % 60 == 0:
                    print(f"{ec.iso(ec.utc_nu())} waarschuwing: meting mislukt ({mislukt}x op rij): {e}", file=sys.stderr)
        wacht = interval - (time.monotonic() - t0)
        if wacht > 0:
            time.sleep(min(wacht, interval))
    db = st["db"]
    db.close()
    print("ems_meterd gestopt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
