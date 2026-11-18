# HEMS-meetsysteem: installer

*English summary below.*

Een eenvoudig thuis-energiemeetsysteem voor een Linux-pc, te installeren **zonder AI en zonder terminalkennis**.
Het leest je digitale meter uit via een lokale P1-dongle, bewaart de metingen en toont ze op een webdashboard,
met je kosten zoals op een factuur, je kwartierpieken (capaciteitstarief) en een dagrapport.

Het systeem **meet en rapporteert alleen**: het schrijft nooit naar een toestel. Alles draait lokaal, zonder cloud.

> Wil je liever zelf, met een AI-assistent, een systeem op maat bouwen (ook met sturing van batterij, laadpaal, ...)?
> Gebruik dan de template: [home-energy-management-system-template](https://github.com/luc-vancraen/home-energy-management-system-template).
> Deze installer is volgens die template gebouwd.

## Wat het doet

- **Meter uitlezen** elke 10 seconden (HomeWizard P1, lokale API v1, of een dongle met hetzelfde formaat).
- **Dashboard** in de browser: live vermogen, kwartierbudget, maandpiek, dagtotalen, grafieken, CSV-export.
- **Kosten zoals op je factuur**: vast, dag/nacht, variabel, dynamisch of blok + variabel; netkosten, heffingen,
  capaciteitstarief (gemiddelde van 12 maandpieken), vaste kosten en btw.
- **Netkosten per gemeente** (Vlaanderen): kies je gemeente en het systeem vult de officiële Fluvius-tarieven 2026
  in, plus de federale heffingen. Groenestroom- en WKK-certificaten krijgen een gangbare waarde die je met je
  tariefkaart vergelijkt.
- **Dagrapport** elke ochtend, optioneel per mail.
- **Marktprijzen** (day-ahead) via ENTSO-E (gratis token) of EnergyZero.
- **Pagina Instellingen**: meter zoeken met één klik, contract, gemeente, mail, wachtwoord. Geen configuratiebestanden
  bewerken.

## Wat heb je nodig

- Een pc met **Linux** en systemd, met of zonder bureaublad, die aan blijft staan. Getest op Debian 13 (GNOME);
  andere gangbare distributies (Ubuntu, Linux Mint, ...) met python3 ≥ 3.9 zouden moeten werken.
- Een **digitale meter** met geactiveerde gegevenspoort (in Vlaanderen via "Mijn Fluvius").
- Een **HomeWizard P1 Meter** (of compatibele dongle) met de **lokale API** aangezet in de HomeWizard-app.
- Liefst een vast IP-adres voor de dongle in je router.

Zonder meter kan je het systeem eerst verkennen in de demo-modus.

## Installeren

1. Download **`hems-systeem-install.sh`** van de laatste
   [release](https://github.com/luc-vancraen/home-energy-management-system-installer/releases/latest).
2. Start het bestand:
   - **met bureaublad**: open een terminal in de map Downloads en typ `bash hems-systeem-install.sh`.
     Daarna gaat alles met vensters: welkom, je wachtwoord, een voortgangsbalk, en op het einde opent je browser
     de pagina Instellingen.
   - **zonder bureaublad** (server, via SSH): `bash hems-systeem-install.sh` toont menu's in de terminal.
3. Noteer de **instelcode** op het einde: daarmee kies je de eerste keer je beheerwachtwoord.
4. Klik op de pagina Instellingen op **Zoek mijn meter**, dan **Test** en **Opslaan**. Kies je contract en je gemeente.

Het dashboard staat daarna op `http://<ip-van-de-pc>:8080`. Op een pc met bureaublad komt er een knop
**HEMS Dashboard** in het menu (bij GNOME ook in het dock).

**Bijwerken**: download de nieuwe versie en start ze opnieuw. Je instellingen en metingen blijven bewaard; vorige
programmabestanden komen in `backup/`.

**Verwijderen**: `bash ~/hems-systeem/verwijder.sh` (met `--alles` ook je metingen).

## Goed om te weten

- De **Fluvius-tarieven** gelden voor 2026. Elk jaar komt er een nieuwe versie met de nieuwe tarieven; het
  dashboard waarschuwt als ze verlopen zijn.
- Brussel, Wallonië en Nederland: de netkosten vul je zelf in vanaf je factuur.
- Het programma is Nederlandstalig.
- Vragen of fouten: open een [issue](https://github.com/luc-vancraen/home-energy-management-system-installer/issues).

## Opbouw van deze repository

- `installer-kop.sh`: het installatiescript (vensters, menu's, services).
- `src/`: het programma zelf (Python 3, alleen de standaardbibliotheek) en de handleiding `LEESMIJ.md`.
- `bouw.py`: voegt alles samen tot één bestand `uit/hems-systeem-install.sh` met sha256-controle per bestand.

## Licentie

MIT, zie [LICENSE](LICENSE). Copyright (c) 2026 luc-vancraen. Zonder enige garantie: controleer je kosten altijd
met je echte factuur.

---

## English summary

A simple home energy monitoring system for a Linux PC, installable **without AI and without terminal skills**.
It reads your digital meter through a local P1 dongle (HomeWizard P1, local API v1), stores the readings and shows
them on a web dashboard with costs like on your bill, quarter-hour peaks (Flemish capacity tariff) and a daily
report. It only measures and reports, never controls a device, and runs fully local.

Download `hems-systeem-install.sh` from the latest release and run `bash hems-systeem-install.sh`: on a desktop
it uses windows (zenity), on a server menus (whiptail). Then open the Settings page to find your meter and enter your
contract and municipality. The user interface is in Dutch; grid tariffs are built in for Flanders (Fluvius 2026).

Prefer to build a tailored system with an AI assistant? Use the
[template](https://github.com/luc-vancraen/home-energy-management-system-template). Licence: MIT.
