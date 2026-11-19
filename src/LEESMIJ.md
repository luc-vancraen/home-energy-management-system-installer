# HEMS-meetsysteem

Een klein thuis-energiemeetsysteem voor een Linux-pc: het leest je digitale meter
uit via een lokale dongle, bewaart de metingen, en toont ze op een webdashboard
met dagrapporten. Gebouwd volgens het HEMS-template (`hems-template-nl.md`),
profiel "Inzicht": **meten en rapporteren, niet sturen**. Het systeem schrijft
nooit naar een toestel.

Het draait volledig lokaal op standaard Python 3 (geen extra Python-pakketten,
geen cloud, geen AI-platform nodig na de installatie).

## Wat het doet

- **Meter uitlezen** om de 10 seconden (HomeWizard P1 lokale API v1, of een dongle
  met hetzelfde JSON-formaat) en opslaan in SQLite.
- **Kwartiergemiddelden en maandpiek** zelf berekenen uit de tellerstanden
  (Vlaams capaciteitstarief; pieken onder 2,5 kW tellen niet mee).
- **Dashboard** op `http://<ip-van-de-pc>:8080`: live vermogen, kwartierbudget,
  maandpiek, dagtotalen en kosten, grafiek, kwartiertabel, marktprijzen en
  CSV-export.
- **Tabletweergave** op `http://<ip-van-de-pc>:8080/tablet`: donker, groot, voor een
  tablet aan de muur (live stroom, kwartierbudget, prijzen, kosten, dagverloop).
- **Dagrapport** (HTML) elke ochtend over gisteren, met oordeel OK/AANDACHT,
  top-5 kwartierpieken en grafiek; optioneel per mail.
- **Marktprijzen** dagelijks ophalen als een provider-API geconfigureerd is
  (EnergyZero zonder token, of ENTSO-E met gratis token).

## Vooraf zelf regelen (kan het systeem niet voor je doen)

1. De gegevenspoort van je digitale meter activeren (in Vlaanderen via
   "Mijn Fluvius"; kan enkele dagen duren).
2. De dongle plaatsen en koppelen aan wifi/netwerk via de app van de fabrikant.
3. Bij HomeWizard: **lokale API v1 aanzetten** in de app (Instellingen → Meter → API).
4. De dongle een **vast IP-adres** geven in je router (DHCP-reservatie).

## Installatie

```bash
bash hems-systeem-install.sh
```

De installer controleert de afhankelijkheden (python3 ≥ 3.9, tzdata, systemd),
installeert wat ontbreekt via apt (daarvoor vraagt hij je sudo-wachtwoord),
en **vraagt tijdens de installatie**:

- de map (standaard `~/hems-systeem`),
- de **meter-API**: de URL van je P1-dongle, bv. `http://192.168.1.50/api/v1/data`
  (leeg = demo-modus met gesimuleerde metingen om te verkennen; de meter kies je
  later gewoon in het dashboard, op de pagina **Instellingen**),
- de **provider-API** voor marktprijzen (optioneel): URL en eventueel een token.
  Het token komt in `.env` (alleen leesbaar voor jou), nooit in de config of in git,
- de dashboardpoort (standaard 8080).

Daarna installeert en start hij drie services en een timer
(`hems-systeem-meter`, `hems-systeem-dashboard`, `hems-systeem-prijzen.timer`, `hems-systeem-rapport.timer`; tot versie 1.0 heetten ze `ems-*`, een update zet dat vanzelf om)
en toont hij meteen een eerste meting. Op een pc met bureaubladomgeving komt er
ook een snelkoppeling **HEMS Dashboard** (met icoon) in het toepassingenmenu en op
het bureaublad, en bij GNOME (dat geen bureaubladpictogrammen toont) in het dock,
die het dashboard opent in de browser.

Handige opties: `--dir MAP`, `--meter-url URL`, `--provider-url URL`,
`--poort N`, `--demo`, `--geen-services`, `--geen-snelkoppeling`, `--force`, `--help`.

### Zonder services (`--geen-services` of geen systemd)

Dan start niets vanzelf. Start de twee programma's met de hand, elk in een eigen
terminal (of via je eigen opstartmechanisme):

```
cd ~/hems-systeem && python3 app/ems_meterd.py --config config/ems.ini      # metingen
cd ~/hems-systeem && python3 app/ems_dashboard.py --config config/ems.ini   # dashboard
```

Marktprijzen ophalen (normaal 3 keer per dag): `python3 app/ems_prijzen.py --config config/ems.ini`.
Het dagrapport: `python3 app/ems_rapport.py --config config/ems.ini`.

## Instellingen in het dashboard

Open `http://<ip-van-de-pc>:8080/instellingen` (of klik op "instellingen" bovenaan
het dashboard).

- **Eerste keer**: kies een beheerwachtwoord. Daarvoor vraagt de pagina de
  eenmalige **instelcode** die de installer op het einde toonde (ook te vinden in
  `config/instelcode`). Zo kan niemand anders op je netwerk het wachtwoord kiezen.
- **Meter**: klik op **Zoek mijn meter**; het systeem zoekt HomeWizard-toestellen op
  je thuisnetwerk (alleen lezen) en toont ze in een keuzelijst. **Test** leest één
  meting en toont je huidige vermogen. **Opslaan** zet het adres in de config.
- Wijzigingen gaan binnen ca. 10 seconden in; herstarten is niet nodig. Bij elke
  wijziging komt er een back-up van de config (`config/ems.ini.bak.*`). Bij een update bewaart de installer de vorige versie van gewijzigde programmabestanden in `backup/` (de laatste 5 updates).
- Het beheerwachtwoord staat alleen als hash in `.env`. Vergeten? Verwijder de
  regel `HEMS_BEHEER_WACHTWOORD` uit `.env` en kies een nieuw wachtwoord met de
  instelcode uit `config/instelcode`.

## Later sturing met AI: vragenlijst "Mijn installatie"

Dit systeem meet alleen. Wil je later je batterij, laadpaal of warmtepomp laten sturen, dan kan een AI-assistent
dat bouwen met het HEMS-template. Open in Instellingen het onderdeel **Later sturing met AI** en vul de vragenlijst
in. Het systeem maakt daarvan `mijn-installatie.md` in deze map. Geef dat bestand samen met het template aan de
assistent: die vraagt dan enkel nog wat ontbreekt, en bouwt verder op dit meetsysteem zonder het te wijzigen.

## Tabletweergave en kaarten op de tablet

Open `http://<ip-van-de-pc>:8080/tablet` op een tablet of oud scherm (knop rechtsboven = volledig scherm;
zet "scherm uit" op de tablet op nooit). Je ziet de live stroom, het kwartierbudget met de pieken van de maand,
de marktprijzen, wat vandaag gebeurde tegenover gisteren, het weer met de zoninstraling en het verloop van vandaag.
Weer uitzetten: Instellingen > Dashboard.

Zon, thuisbatterij, auto, warmtepomp ... meet dit systeem niet zelf. Een ander programma (bv. een module die je
met de AI bouwt) kan ze wel op de tablet zetten door per toestel een klein JSON-bestand te schrijven in
`data/tablet/` in de projectmap. Toestellen die in je vragenlijst staan maar nog geen kaart hebben, verschijnen als
"nog niet gekoppeld". Een kaart met `stroom_w` komt ook in het stroomschema bovenaan.
Een eigen foto (bv. van je auto) laad je op bij Instellingen > Dashboard > "Foto's voor de tablet".

```
Een kaart = één bestand data/tablet/<naam>.json (UTF-8, hoogstens 20 kB), bv.:
{
  "titel": "Auto",                 verplicht
  "soort": "auto",                 auto | batterij | zon | laadpaal | warmtepomp | boiler | stekker | ander
  "volgorde": 10,                  kleiner = eerder (standaard 50)
  "status": "ok",                  ok | let | fout | uit | info   (kleur van het bolletje)
  "statustekst": "laadt",          korte toestand naast het bolletje
  "waarde": "72", "eenheid": "%", "label": "batterij",     het grote getal
  "balk": 72,                      0..100, optioneel (laadtoestand)
  "regels": [["Bereik", "288 km"], ["Laadstroom", "16 A"]],
  "stroom_w": -3680,               optioneel: toont het toestel in het stroomschema
                                   (+ = levert aan het huis, - = neemt van het huis)
  "bijgewerkt": "2026-11-19T10:00:00Z",   UTC; zonder dit telt de wijzigingstijd van het bestand
  "geldig_s": 300                  daarna toont de tablet 'verouderd' (standaard 300 s)
}
Schrijf eerst naar een tijdelijk bestand en hernoem het dan (os.replace), zodat de
tablet nooit een half bestand leest.
```

## Dagelijks gebruik

- Dashboard: `http://<ip-van-de-pc>:8080`
- Instellingen: `http://<ip-van-de-pc>:8080/instellingen`, of met de hand in
  `~/hems-systeem/config/ems.ini` (wordt binnen ca. 10 s zelf opnieuw ingelezen)
- Logboeken: `journalctl -u hems-systeem-meter -f`
- Een meting testen: `python3 ~/hems-systeem/app/ems_meterd.py --config ~/hems-systeem/config/ems.ini --eenmalig`
- Rapport nu maken: `python3 ~/hems-systeem/app/ems_rapport.py --config ~/hems-systeem/config/ems.ini`

## Tarieven invullen

Zet in `config/ems.ini` onder `[tarief]` je echte contractprijzen, anders zijn de
kosten 0. Controleer de berekening tegen een factuur; tarieven veranderen
(meestal op 1 januari): pas ze dan aan.

## Verwijderen

```bash
bash ~/hems-systeem/verwijder.sh           # stopt en verwijdert de services
bash ~/hems-systeem/verwijder.sh --alles   # verwijdert ook alle data en bestanden
```

## Veiligheid en privacy

- Het systeem **leest alleen**. Er is geen stuurcode, dus het kan geen toestel
  beïnvloeden. Wil je later ook sturen (batterijen, laadpaal): gebruik dan het
  HEMS-template met een AI-agent, met het bijbehorende veiligheidsproces.
- Je verbruiksdata is privé (het verraadt wanneer je thuis bent). Zet een
  wachtwoord op het dashboard als je netwerk gedeeld is, en open nooit een
  poort in je router naar dit systeem.
