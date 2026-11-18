# Wijzigingen

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
