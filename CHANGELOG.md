# Wijzigingen

## 1.4.0 (19 november 2026)

- **Versie zichtbaar**: onderaan het dashboard, de rapportenlijst en Instellingen staat de geïnstalleerde versie
  (de installer schrijft ze in het bestand `VERSIE`).
- **Melding bij een nieuwe versie**: het dashboard kijkt hoogstens één keer per dag op GitHub of er een nieuwere
  release is en toont dan bovenaan hoe je bijwerkt. Enkel melden, nooit zelf bijwerken; er gaat niets van jou mee.
  Uit te zetten bij Instellingen > Algemeen (`[update] controleren`).
- **Elke ochtend een back-up van je metingen**: samen met het dagrapport een gecomprimeerde kopie van de databank
  in `backup-db/` (de laatste 7), veilig terwijl het systeem meet. Optioneel ook naar een USB-stick of netwerkmap
  (Instellingen > Algemeen, `[opslag] backup_map`). Het dagrapport zegt of het gelukt is; terugzetten staat in LEESMIJ.
- Vragenlijst, stap **Toestellen zoeken**: korte uitleg in 3 stappen en de knoppen **Klaar** en **Opslaan en verder**,
  zoals bij de andere stappen (de gebruiker wist niet hoe verder te gaan).
- README: rubriek **Problemen?** met de meest gestelde vragen (meter niet gevonden, fout 599, dubbelklikken op
  `.sh`, dashboard niet op gsm, wachtwoord vergeten, kosten anders dan factuur, `bouw.py`), ook kort in het Engels.

## 1.3.2 (19 november 2026)

- Marktprijzen: bij een storing bij de bron (fout 5xx, bv. "fout 599" van ENTSO-E) zegt de melding nu dat de bron
  tijdelijk onbereikbaar is en dat het niet aan je token of instellingen ligt, in plaats van enkel "fout 599".
- README: stap voor stap installeren op een nieuwe Debian met bureaublad (ook in het Engels).

## 1.3.1 (19 november 2026)

- **Oogje** naast elk wachtwoordveld (beheerwachtwoord, kijkwachtwoord, mailwachtwoord, ENTSO-E-token, aanmelden):
  tik erop om te zien wat je typt, nog eens om het weer te verbergen. Handig bij lange tokens of op een tablet.

## 1.3.0 (19 november 2026)

- **Nieuw: starter `hems-bouwen-met-ai.sh`** (versie 1.1.0, apart bestand in de release): installeert een AI-agent
  (Claude Code, OpenAI Codex CLI, Gemini CLI of Kimi Code), haalt de laatste template op, maakt de projectmap
  `~/hems-ai` met git en zet een knop "HEMS bouwen met AI" in het menu. Stuurt niets aan.
- **Tabletweergave** op `/tablet`: een donkere pagina voor een tablet aan de muur of een oud scherm, met live
  energiestroom tussen net en huis (en per fase t.o.v. de hoofdzekering), het lopende kwartier tegenover het budget
  van het capaciteitstarief (met aftellen), de marktprijzen van vandaag en morgen (goedkoop/duur gekleurd, goedkoopste
  uur van de komende 12 u), afname, teruglevering en kosten van vandaag en deze maand, en het vermogen van vandaag.
  Werkt staand, liggend en op een gsm; knop voor volledig scherm; geen internet of externe bibliotheken nodig.
  Link "tabletweergave" bovenaan het dashboard, uitleg bij Instellingen > Dashboard.
- **Kaarten op de tablet**: andere programma's (bv. sturingsmodules gebouwd met de AI) tonen zon, batterij, auto,
  laadpaal, warmtepomp ... door een JSON-bestand te schrijven in `data/tablet/` (formaat in LEESMIJ). Met
  `stroom_w` komt het toestel ook in het stroomschema (zon/batterij/net naar huis, huis naar auto). Toestellen uit de
  vragenlijst zonder kaart verschijnen als "nog niet gekoppeld". Het meetsysteem leest die bestanden alleen.
- **Eigen foto's op de tablet**: bij Instellingen > Dashboard een foto opladen per toestel (auto, batterij, zon, ...);
  de browser verkleint ze eerst, ze blijft op de pc en verschijnt groot op de kaart van dat toestel. Een PNG met
  doorzichtige achtergrond (bv. een uitgeknipte auto) staat zonder vak en met een schaduw op de kaart.
- **Gas en water** op de tablet: verbruik van vandaag (m³ en liter) met vergelijking met gisteren tot hetzelfde uur,
  zodra de digitale meter een gas- of watermeter doorgeeft.
- **Gas- en watertarief** bij Instellingen > Gas en water (gas per kWh zoals op de factuur, met omrekening m³ naar kWh,
  netkosten en heffingen, vaste kosten en btw; water per m³ en vaste kosten, met btw). De tablet toont de kosten van
  vandaag en van deze maand in de tegels gas en water.
- Vragenlijst: nieuwe vraag "Hoeveel personen zijn er gedomicilieerd?" (verbruik inschatten, basistarief water).
- **Fout opgelost**: de waterstand werd nooit opgeslagen (verkeerde veldnaam); gas en water komen nu uit de lijst
  `external` van de HomeWizard P1 Meter (gas ook nog uit `total_gas_m3`).
- Tablet: **weer en zoninstraling** (Open-Meteo, gratis, locatie uit de gemeente; uit te zetten bij Instellingen >
  Dashboard), **hoogste kwartieren van de maand** en **vergelijking met gisteren tot hetzelfde uur**.
- `mijn-installatie.md` bevat nu ook de bedragen van je contract (energieprijs of formule, blok, injectie, netkosten
  en heffingen, vaste kosten, btw) zoals ingevuld op de pagina Instellingen, zodat de AI-assistent ze niet opnieuw
  vraagt. Ontbreekt de leverancier of de hoofdzekering, dan staat er "niet ingevuld".
- Installer: het slotoverzicht toont ook het adres van de tablet; met `--geen-services` staat er hoe je het systeem
  met de hand start (in plaats van commando's voor services die er niet zijn). LEESMIJ heeft daar nu een stukje over.
- Geen lege tussenkopjes meer, en de echte projectmap staat in de uitleg voor de AI.
- `verwijder.sh` weigert onbekende opties (vroeger startte bv. `verwijder.sh --versie` gewoon het verwijderen).

## 1.2.0 (18 november 2026)

- Nieuw: vragenlijst **Mijn installatie** (Instellingen → "Later sturing met AI"), voor wie later met een
  AI-assistent sturing wil laten bouwen volgens het HEMS-template. De vragen volgen blok A tot I van het template
  (doelen, aansluiting, zon, batterij, auto, toestellen, machine, weergave). Wat het systeem al weet (meter, fasen,
  gemeente, netbeheerder, contract, maandpiek), vult het zelf in.
- De antwoorden komen in `mijn-installatie.md` in de projectmap, te downloaden vanaf de pagina. De AI-assistent leest
  dat bestand en vraagt enkel nog wat ontbreekt.
- Toestellen zoeken op het thuisnetwerk (alleen lezen): HomeWizard, go-e Charger, Shelly. Thuisbatterijen worden
  bewust niet aangesproken (veel batterijen aanvaarden maar één verbinding).
- Het meetsysteem zelf verandert er niet door en stuurt nog altijd niets.

## 1.1.0 (18 november 2026)

- Eigen servicenamen: `hems-systeem-meter`, `hems-systeem-dashboard`, `hems-systeem-prijzen` en `hems-systeem-rapport`
  (waren `ems-*`). Zo botst het systeem niet meer met een ander systeem dat dezelfde algemene namen gebruikt.
- Bijwerken vanaf 1.0 zet de oude services van deze installatie vanzelf om. Services met een oude naam die bij een
  ander systeem horen, blijven onaangeroerd.
- `verwijder.sh` verwijdert enkel services die bij de eigen projectmap horen.
- Staat er al een HEMS-meetsysteem in een andere map, dan stopt de installer met uitleg in plaats van het te
  overschrijven.

## 1.0.0 (18 november 2026)

Eerste publieke versie.

- Installeren met vensters (zenity) op een pc met bureaublad, met menu's (whiptail) in een terminal, of met
  tekstvragen en opties voor scripts. Sudo-wachtwoord in een venster, voortgangsbalk, vrije poort, op het einde
  opent de browser de pagina Instellingen.
- Pagina Instellingen in het dashboard: meter zoeken en testen, contract, marktprijzen, installatie, mail,
  dashboardwachtwoord, tijdzone. Wijzigingen werken zonder herstart.
- Kosten zoals op een factuur: vast, dag/nacht, variabel, dynamisch en blok + variabel; maandindex officieel of
  geschat; netkosten, heffingen, capaciteitstarief op het gemiddelde van 12 maandpieken, vaste kosten, btw.
- Netkosten per gemeente (Vlaanderen): 285 gemeenten, 8 netgebieden, officiële Fluvius-tarieven 2026.
- Federale heffingen automatisch; groenestroom- en WKK-certificaten met een gangbare waarde.
- Snelkoppeling "HEMS Dashboard" in menu, op het bureaublad en bij GNOME in het dock.
- Updates bewaren de vorige programmabestanden in `backup/` (laatste 5 updates).
- Getest op Debian 13 (GNOME, Wayland) in een virtuele machine: installeren en bijwerken met vensters, meter zoeken
  op het thuisnetwerk. Ook in tekstmodus zonder bureaublad.
