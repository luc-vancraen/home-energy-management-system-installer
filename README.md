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
- **Tabletweergave** op `/tablet`: een donkere pagina voor een tablet aan de muur, met de energiestroom, het
  lopende kwartier, marktprijzen, weer en zonprognose, gas en water, en kaarten (met eigen foto) voor zon,
  batterij of auto die andere programma's aanleveren.
- **Gas en water** uit dezelfde digitale meter, met je gas- en watertarief: verbruik en kosten van vandaag en deze maand.
- **Pagina Instellingen**: meter zoeken met één klik, contract, gemeente, mail, wachtwoord. Geen configuratiebestanden
  bewerken.

## Wat heb je nodig

**Om te meten**
- Een **digitale meter** met geactiveerde gegevenspoort (in Vlaanderen gratis via "Mijn Fluvius").
- Een **HomeWizard P1 Meter** (of een dongle met hetzelfde formaat) aan die poort, met de **lokale API** aangezet
  in de HomeWizard-app.
- Liefst een vast IP-adres voor de dongle in je router (niet verplicht: "Zoek mijn meter" vindt hem ook zo).

Zonder meter kan je het systeem eerst verkennen in de demo-modus.

**De computer**
- **Linux**, met of zonder bureaublad. Getest op Debian 13 (GNOME); andere gangbare distributies (Ubuntu,
  Linux Mint, Raspberry Pi OS, ...) zouden moeten werken.
- **python3 ≥ 3.9** met sqlite3 en tzdata. Dat zit standaard in gangbare distributies; ontbreekt het, dan installeert
  de installer het via apt. Geen extra Python-pakketten, geen Docker, geen cloud.
- Weinig vermogen nodig: samen ongeveer 70 MB geheugen en zo'n 400 MB schijfruimte per jaar aan metingen. Een oude
  laptop, een mini-pc of een Raspberry Pi volstaat.
- De pc blijft **aan staan** en zit in **hetzelfde thuisnetwerk** als de P1-dongle.

**Rechten**
- Een gebruiker met **sudo-wachtwoord**, zodat de services vanzelf starten. Dat is het enige moment dat de installer
  om een wachtwoord vraagt.
- Zonder sudo of zonder systemd (bv. WSL) lukt de installatie ook (of met `--geen-services`), maar dan start je de
  twee programma's met de hand. Het slotoverzicht en `LEESMIJ.md` zeggen hoe.

**Kennis**
- Geen terminalkennis: met bureaublad gaat het met vensters, op een server met menu's. Je start één keer
  `bash hems-systeem-install.sh`; daarna gaat alles via de pagina Instellingen.

**Optioneel**
- **Internet** voor de marktprijzen, het weer op de tablet en de mail met het dagrapport. Het meten zelf werkt zonder.
- Een gratis **ENTSO-E-token** voor de marktprijzen (zonder token: EnergyZero).
- Een **tablet of oud scherm** met een browser voor de tabletweergave.
- Voor de starter **HEMS bouwen met AI**: internet en een account bij de aanbieder van de AI die je kiest.

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

## Verder bouwen met AI: de starter

Wil je daarna ook sturen (batterij, laadpaal, ...)? Download **`hems-bouwen-met-ai.sh`** van dezelfde release en
start het met `bash hems-bouwen-met-ai.sh`. Het installeert een AI-agent naar keuze (Claude Code, OpenAI Codex CLI,
Gemini CLI of Kimi Code), haalt de laatste [template](https://github.com/luc-vancraen/home-energy-management-system-template)
op, maakt een projectmap `~/hems-ai` en zet een knop **HEMS bouwen met AI** in het menu. De AI leest eerst je
antwoorden uit de vragenlijst (`~/hems-systeem/mijn-installatie.md`) en bouwt dan samen met jou, stap voor stap.
Je hebt een account nodig bij de aanbieder van de AI die je kiest.

## Goed om te weten

- De **Fluvius-tarieven** gelden voor 2026. Elk jaar komt er een nieuwe versie met de nieuwe tarieven; het
  dashboard waarschuwt als ze verlopen zijn.
- Brussel, Wallonië en Nederland: de netkosten vul je zelf in vanaf je factuur.
- Het programma is Nederlandstalig.
- Vragen of fouten: open een [issue](https://github.com/luc-vancraen/home-energy-management-system-installer/issues).

## Opbouw van deze repository

- `installer-kop.sh`: het installatiescript (vensters, menu's, services).
- `src/`: het programma zelf (Python 3, alleen de standaardbibliotheek) en de handleiding `LEESMIJ.md`.
- `starter/hems-bouwen-met-ai.sh`: de starter om verder te bouwen met een AI-agent.
- `bouw.py`: voegt alles samen tot één bestand `uit/hems-systeem-install.sh` met sha256-controle per bestand.

## Licentie

MIT, zie [LICENSE](LICENSE). Copyright (c) 2026 luc-vancraen. Zonder enige garantie: controleer je kosten altijd
met je echte factuur.

---

## English summary

A simple home energy monitoring system for a Linux PC, installable **without AI and without terminal skills**.
It reads your digital meter through a local P1 dongle (HomeWizard P1, local API v1), stores the readings and shows
them on a web dashboard with costs like on your bill, quarter-hour peaks (Flemish capacity tariff) and a daily
report, plus a wall-tablet view (`/tablet`) and gas and water usage and costs. It only measures and reports,
never controls a device, and runs fully local.

Download `hems-systeem-install.sh` from the latest release and run `bash hems-systeem-install.sh`: on a desktop
it uses windows (zenity), on a server menus (whiptail). Then open the Settings page to find your meter and enter your
contract and municipality. The user interface is in Dutch; grid tariffs are built in for Flanders (Fluvius 2026).

**What you need**

- *To measure*: a digital meter with its data port enabled (in Flanders free via "Mijn Fluvius") and a HomeWizard P1
  Meter (or a dongle with the same format) with the **local API** enabled in the HomeWizard app. A fixed IP address
  for the dongle is nice but not required. Without a meter you can explore the demo mode first.
- *The computer*: **Linux**, with or without a desktop (tested on Debian 13 GNOME; Ubuntu, Linux Mint, Raspberry Pi
  OS, ... should work), **python3 ≥ 3.9** with sqlite3 and tzdata (standard in common distributions; installed via apt
  if missing). No extra Python packages, no Docker, no cloud. About 70 MB of memory and roughly 400 MB of disk per
  year of readings: an old laptop, a mini PC or a Raspberry Pi is enough. It stays on, in the same home network as
  the P1 dongle.
- *Rights*: a user with a **sudo password**, so the services start automatically (the only time the installer asks
  for a password). Without sudo or systemd (e.g. WSL), or with `--geen-services`, it still installs, but you start
  the two programs by hand; the end summary and `LEESMIJ.md` explain how.
- *Skills*: no terminal skills: windows on a desktop, menus on a server. You run `bash hems-systeem-install.sh` once;
  everything else is done on the Settings page.
- *Optional*: internet for market prices, the weather on the tablet and the daily report by mail (measuring works
  without); a free ENTSO-E token for market prices (otherwise EnergyZero); a tablet or old screen with a browser for
  the tablet view; for the AI starter: internet and an account with the AI provider you choose.

To go further with an AI agent, run `hems-bouwen-met-ai.sh` from the same release: it installs an agent of your
choice, fetches the template and adds a "HEMS bouwen met AI" button. Or use the
[template](https://github.com/luc-vancraen/home-energy-management-system-template). Licence: MIT.
