#!/usr/bin/env bash
# HEMS bouwen met AI — starter
#
# Zet alles klaar om met een AI-agent een energiebeheersysteem op maat te bouwen
# volgens het HEMS-template, zonder terminalkennis:
#   1. installeert een AI-agent (Claude Code, OpenAI Codex CLI, Gemini CLI of Kimi Code) als die er nog niet is;
#   2. haalt de laatste versie van het template op;
#   3. maakt een projectmap met git;
#   4. zet een knop "HEMS bouwen met AI" in het menu (bij GNOME ook in het dock) die een terminal
#      opent met de agent al gestart. De eerste keer meldt de agent je zelf aan (je browser opent).
#
# Het systeem zelf bouwt de AI daarna samen met jou, stap voor stap en met jouw akkoord.
# Deze starter stuurt niets aan en wijzigt niets aan een bestaand HEMS-meetsysteem.
#
# Gebruik: bash hems-bouwen-met-ai.sh            (vensters of vragen in de terminal)
#          bash hems-bouwen-met-ai.sh --verwijder (knop weghalen; projectmap blijft)
#          bash hems-bouwen-met-ai.sh --versie
#
# MIT-licentie, zie LICENSE in de repository home-energy-management-system-installer.

set -uo pipefail

VERSIE="1.1.0"
TITEL="HEMS bouwen met AI"
TEMPLATE_REPO="luc-vancraen/home-energy-management-system-template"
STANDAARD_MAP="$HOME/hems-ai"
HULPMAP="$HOME/.local/share/hems-ai"          # startscript + instellingen van de knop
APPS="$HOME/.local/share/applications"
DESKTOP_NAAM="hems-bouwen-met-ai.desktop"
LOG="$HOME/hems-bouwen-met-ai.log"
export PATH="$HOME/.local/bin:$PATH"

UI=auto
ACTIE=installeer
TEMPLATE_BESTAND=""          # voor tests: een lokaal templatebestand i.p.v. de laatste release
while [ $# -gt 0 ]; do
    case "$1" in
        --verwijder)        ACTIE=verwijder; shift ;;
        --gui)              UI=gui; shift ;;
        --tekst)            UI=tekst; shift ;;
        --template-bestand) [ $# -ge 2 ] || { echo "--template-bestand vereist een bestand" >&2; exit 1; }; TEMPLATE_BESTAND="$2"; shift 2 ;;
        --versie|--version) echo "$VERSIE"; exit 0 ;;
        --help|-h)          sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)                  echo "Onbekende optie: $1 (probeer --help)" >&2; exit 1 ;;
    esac
done

if [ "$UI" = auto ]; then
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null 2>&1; then UI=gui; else UI=tekst; fi
fi

# ---- vensters of tekst --------------------------------------------------------

: > "$LOG"
log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" >> "$LOG"; }

melding() {
    if [ "$UI" = gui ]; then zenity --info --title="$TITEL" --width=540 --text="$1" 2>/dev/null || true
    else printf '\n%b\n\n' "$1" | sed 's/<[^>]*>//g'; fi
}
ja() {   # $1 tekst, $2 knop ja, $3 knop nee
    if [ "$UI" = gui ]; then
        zenity --question --title="$TITEL" --width=540 --text="$1" --ok-label="$2" --cancel-label="$3" 2>/dev/null
    else
        local a; printf '\n%b\n' "$1" | sed 's/<[^>]*>//g'; read -r -p "$2? [j/N] " a; [[ "$a" =~ ^[jJyY] ]]
    fi
}
stop() {
    log "FOUT $*"
    voortgang_stop
    if [ "$UI" = gui ]; then
        zenity --error --title="$TITEL" --width=540 --text="Het klaarzetten is gestopt:\n\n$*\n\nDetails staan in $LOG" 2>/dev/null || true
    else
        printf '\nFOUT: %b\n(details in %s)\n' "$*" "$LOG" >&2
    fi
    exit 1
}
VOORTGANG=0
voortgang_start() {
    if [ "$UI" = gui ]; then
        exec 3> >(zenity --progress --pulsate --auto-close --no-cancel --title="$TITEL" --width=480 \
                         --text="Bezig ..." 2>/dev/null || true)
        VOORTGANG=1
    fi
}
stap() {
    log "==> $*"
    if [ "$VOORTGANG" = 1 ]; then printf '# %s\n' "$*" >&3 2>/dev/null || true; else printf '==> %s\n' "$*"; fi
}
voortgang_stop() {
    if [ "$VOORTGANG" = 1 ]; then printf '100\n' >&3 2>/dev/null || true; exec 3>&- || true; VOORTGANG=0; sleep 0.3; fi
}

# sudo vraagt zijn wachtwoord in een venster
if [ "$UI" = gui ]; then
    ASKPASS=$(mktemp "${TMPDIR:-/tmp}/hems-ai-askpass.XXXXXX")
    printf '#!/bin/sh\nexec zenity --password --title="HEMS bouwen met AI: wachtwoord nodig" 2>/dev/null\n' > "$ASKPASS"
    chmod 700 "$ASKPASS"
    export SUDO_ASKPASS="$ASKPASS"
    trap 'rm -f "$ASKPASS"' EXIT
    alssudo() { command sudo -A "$@"; }
else
    alssudo() { command sudo "$@"; }
fi

pakketten() {   # installeer Debian/Ubuntu-pakketten als ze ontbreken
    command -v apt-get >/dev/null 2>&1 || stop "Kan $* niet installeren: dit is geen Debian of Ubuntu. Installeer $* zelf en start opnieuw."
    stap "Installeren: $* (je wachtwoord is nodig)"
    voortgang_stop     # het wachtwoordvenster mag niet achter de voortgangsbalk verdwijnen
    alssudo apt-get update >> "$LOG" 2>&1 || stop "apt-get update mislukte (wachtwoord fout of geen internet?)."
    voortgang_start
    stap "Installeren: $*"
    alssudo apt-get install -y "$@" >> "$LOG" 2>&1 || stop "Installeren van $* mislukte."
}

# ---- dock (GNOME) ------------------------------------------------------------------

dock_toevoegen() {
    command -v gsettings >/dev/null 2>&1 || return 1
    local fav nieuw
    fav=$(gsettings get org.gnome.shell favorite-apps 2>/dev/null) || return 1
    case "$fav" in
        *"$DESKTOP_NAAM"*) return 0 ;;
        "[]"|"@as []")     nieuw="['$DESKTOP_NAAM']" ;;
        *)                 nieuw="${fav%]}, '$DESKTOP_NAAM']" ;;
    esac
    gsettings set org.gnome.shell favorite-apps "$nieuw" 2>/dev/null
}
dock_weghalen() {
    command -v gsettings >/dev/null 2>&1 || return 0
    local fav
    fav=$(gsettings get org.gnome.shell favorite-apps 2>/dev/null) || return 0
    case "$fav" in *"$DESKTOP_NAAM"*) ;; *) return 0 ;; esac
    fav=$(printf '%s' "$fav" | sed -e "s/, '$DESKTOP_NAAM'//; s/'$DESKTOP_NAAM', //; s/'$DESKTOP_NAAM'//")
    gsettings set org.gnome.shell favorite-apps "$fav" 2>/dev/null || true
}

# ---- verwijderen ---------------------------------------------------------------------

if [ "$ACTIE" = verwijder ]; then
    map=""
    [ -f "$HULPMAP/instellingen" ] && map=$(sed -n 's/^MAP=//p' "$HULPMAP/instellingen" | tr -d "'")
    ja "De knop \"HEMS bouwen met AI\" weghalen?\n\nJe projectmap${map:+ ($map)} en de AI-agent blijven staan. Wat de AI al gebouwd heeft, blijft gewoon draaien." \
       "Weghalen" "Annuleren" || exit 0
    dock_weghalen
    rm -f "$APPS/$DESKTOP_NAAM" "$HULPMAP/start.sh" "$HULPMAP/instellingen"
    rmdir "$HULPMAP" 2>/dev/null || true
    melding "De knop is weg.${map:+\n\nJe projectmap staat nog in $map.}"
    exit 0
fi

# ---- 1. welkom -------------------------------------------------------------------------

meetsysteem=""
for m in "$HOME/hems-systeem" "$HOME"/*/; do
    m="${m%/}"
    if [ -f "$m/config/ems.ini" ] && [ -f "$m/app/ems_meterd.py" ]; then meetsysteem="$m"; break; fi
done
log "starter $VERSIE, meetsysteem='${meetsysteem}'"

if [ -n "$meetsysteem" ]; then
    meet_tekst="Je HEMS-meetsysteem in $meetsysteem blijft het meten doen. De AI bouwt de sturing erbij, als aparte onderdelen in dezelfde stijl, en wijzigt het meetsysteem zelf niet."
else
    meet_tekst="Er staat nog geen HEMS-meetsysteem op deze pc. Dat is niet nodig: de AI bouwt dan ook het meten. Wil je eerst enkel meten zonder AI, gebruik dan de installer (hems-systeem-install.sh)."
fi

if [ -n "$TEMPLATE_BESTAND" ]; then template_regel="neemt het HEMS-template over uit $TEMPLATE_BESTAND (niets te downloaden)"
else template_regel="haalt de laatste versie van het HEMS-template op van internet"; fi

ja "Hiermee zet je alles klaar om met een AI-assistent een energiebeheersysteem op maat te bouwen (batterij, laadpaal, zon, toestellen).\n\nDe starter:\n• installeert een AI-agent (als die er nog niet is)\n• $template_regel\n• maakt een projectmap\n• zet een knop <b>HEMS bouwen met AI</b> in het menu en het dock\n\n$meet_tekst\n\nJe hebt een abonnement of account nodig bij de maker van de AI. De AI vraagt je toestemming voor elke stap en stuurt pas iets aan als jij dat goedkeurt." \
   "Verder" "Annuleren" || exit 0

# ---- 2. welke AI? ------------------------------------------------------------------------

AGENT=""
if [ "$UI" = gui ]; then
    AGENT=$(zenity --list --radiolist --title="$TITEL" --width=620 --height=360 \
        --text="Met welke AI-agent wil je werken?\nKies de AI waarvan je een abonnement of account hebt." \
        --column="" --column="code" --column="AI-agent" --column="nodig" --hide-column=2 --print-column=2 \
        TRUE  claude "Claude Code (Anthropic)"     "Claude-abonnement (Pro/Max) of API-account" \
        FALSE codex  "Codex CLI (OpenAI)"          "ChatGPT-abonnement (Plus/Pro) of API-account" \
        FALSE gemini "Gemini CLI (Google)"         "Google-account (gratis gebruik met limiet)" \
        FALSE kimi   "Kimi Code (Moonshot AI)"     "Kimi-abonnement of Kimi Platform API-sleutel" \
        2>/dev/null) || exit 0
else
    printf '\nMet welke AI-agent wil je werken?\n  1) Claude Code (Anthropic)\n  2) Codex CLI (OpenAI)\n  3) Gemini CLI (Google)\n  4) Kimi Code (Moonshot AI)\n'
    read -r -p "Keuze [1]: " k
    case "${k:-1}" in 2) AGENT=codex ;; 3) AGENT=gemini ;; 4) AGENT=kimi ;; *) AGENT=claude ;; esac
fi
[ -n "$AGENT" ] || exit 0
case "$AGENT" in
    claude) AGENT_NAAM="Claude Code" ;;
    codex)  AGENT_NAAM="Codex CLI" ;;
    gemini) AGENT_NAAM="Gemini CLI" ;;
    kimi)   AGENT_NAAM="Kimi Code" ;;
    *)      stop "Onbekende keuze: $AGENT" ;;
esac
log "agent=$AGENT"

# ---- 3. projectmap -----------------------------------------------------------------------

MAP="$STANDAARD_MAP"
if [ -f "$HULPMAP/instellingen" ]; then
    vorige=$(sed -n 's/^MAP=//p' "$HULPMAP/instellingen" | tr -d "'")
    [ -n "$vorige" ] && MAP="$vorige"
fi
OPNIEUW=0
if [ -f "$MAP/.hems-ai" ]; then
    OPNIEUW=1
    ja "Er staat al een project in $MAP.\n\nDe knop opnieuw maken en het template bijwerken naar de laatste versie? Wat er al gebouwd is en de notities van de AI blijven bewaard." \
       "Bijwerken" "Annuleren" || exit 0
elif [ -e "$MAP" ] && [ -n "$(ls -A "$MAP" 2>/dev/null)" ]; then
    stop "De map $MAP bestaat al en is niet leeg. Verplaats of hernoem die map en start opnieuw."
fi

# ---- 4. benodigdheden --------------------------------------------------------------------

voortgang_start
stap "Controleren wat er al op deze pc staat"
command -v curl >/dev/null 2>&1 || pakketten curl ca-certificates
command -v git  >/dev/null 2>&1 || pakketten git
if { [ "$AGENT" = codex ] || [ "$AGENT" = gemini ]; } && ! command -v "$AGENT" >/dev/null 2>&1; then
    command -v npm >/dev/null 2>&1 || pakketten nodejs npm
    nodever=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
    [ "$nodever" -ge 20 ] 2>/dev/null || stop "$AGENT_NAAM heeft Node.js 20 of nieuwer nodig (hier: ${nodever}). Kies Claude Code, of installeer een nieuwere Node.js."
fi

# ---- 5. AI-agent installeren ---------------------------------------------------------------

if command -v "$AGENT" >/dev/null 2>&1; then
    stap "$AGENT_NAAM staat er al"
else
    stap "$AGENT_NAAM installeren (dit kan een paar minuten duren)"
    case "$AGENT" in
        claude) curl -fsSL https://claude.ai/install.sh | bash >> "$LOG" 2>&1 ;;
        codex)  npm install -g --prefix "$HOME/.local" @openai/codex >> "$LOG" 2>&1 ;;
        gemini) npm install -g --prefix "$HOME/.local" @google/gemini-cli >> "$LOG" 2>&1 ;;
        kimi)   curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash >> "$LOG" 2>&1 ;;
    esac
    hash -r
    if ! command -v "$AGENT" >/dev/null 2>&1; then
        # sommige installatiescripts zetten het programma in een eigen map
        for d in "$HOME/.local/bin" "$HOME/bin" "$HOME/.kimi/bin" "$HOME/.kimi-code/bin" "$HOME/.claude/local"; do
            [ -x "$d/$AGENT" ] && { export PATH="$d:$PATH"; hash -r; break; }
        done
    fi
    command -v "$AGENT" >/dev/null 2>&1 || stop "$AGENT_NAAM installeren is niet gelukt (internet?)."
fi
AGENT_PAD=$(command -v "$AGENT")
log "agent: $AGENT_PAD"

# ---- 6. template ophalen -------------------------------------------------------------------

taal=nl; case "${LANG:-}" in nl*|"") taal=nl ;; *) taal=en ;; esac
TEMPLATE="hems-template-$taal.md"
if [ -n "$TEMPLATE_BESTAND" ]; then
    [ -f "$TEMPLATE_BESTAND" ] || stop "Bestand $TEMPLATE_BESTAND bestaat niet."
    TEMPLATE=$(basename "$TEMPLATE_BESTAND")
    tag="$(sed -n 's/^Template \(v[0-9.]*\).*/\1/p' "$TEMPLATE_BESTAND" | head -1) (lokaal)"
    stap "HEMS-template overnemen uit $TEMPLATE_BESTAND"
    tmp=$(mktemp); cp "$TEMPLATE_BESTAND" "$tmp"
else
stap "Laatste versie van het HEMS-template ophalen van internet"
tag=$(curl -fsSL -m 20 "https://api.github.com/repos/$TEMPLATE_REPO/releases/latest" 2>>"$LOG" \
      | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
[ -n "$tag" ] || stop "Kon de laatste versie van het template niet opvragen bij GitHub (internet?)."
tmp=$(mktemp)
curl -fsSL -m 60 -o "$tmp" "https://raw.githubusercontent.com/$TEMPLATE_REPO/$tag/$TEMPLATE" 2>>"$LOG" \
    || { rm -f "$tmp"; stop "Kon $TEMPLATE ($tag) niet downloaden."; }
fi
head -1 "$tmp" | grep -q '^# ' || { rm -f "$tmp"; stop "Het gedownloade template ziet er niet goed uit."; }
log "template $tag"

# ---- 7. projectmap klaarzetten ----------------------------------------------------------------

stap "Projectmap $MAP klaarzetten"
mkdir -p "$MAP" || stop "Kan $MAP niet maken."
if [ -f "$MAP/$TEMPLATE" ] && ! cmp -s "$tmp" "$MAP/$TEMPLATE"; then
    cp -p "$MAP/$TEMPLATE" "$MAP/$TEMPLATE.vorige"
fi
mv "$tmp" "$MAP/$TEMPLATE"; chmod 644 "$MAP/$TEMPLATE"
printf 'versie=%s\nagent=%s\ntemplate=%s\n' "$VERSIE" "$AGENT" "$tag" > "$MAP/.hems-ai"
if [ ! -d "$MAP/.git" ]; then
    git -C "$MAP" init -q >> "$LOG" 2>&1 || stop "git init in $MAP mislukte."
    naam=$(getent passwd "$USER" | cut -d: -f5 | cut -d, -f1); naam="${naam:-$USER}"
    git -C "$MAP" config user.name "$naam"
    git -C "$MAP" config user.email "$USER@$(hostname -s 2>/dev/null || echo localhost).local"
    printf '%s\n' '.hems-ai' '.env' '*.db' '*.db-wal' '*.db-shm' '.venv/' '__pycache__/' '*.vorige' > "$MAP/.gitignore"
    git -C "$MAP" add -A >> "$LOG" 2>&1
    git -C "$MAP" commit -qm "Start: HEMS-template $tag" >> "$LOG" 2>&1 || true
elif ! git -C "$MAP" diff --quiet -- "$TEMPLATE" 2>/dev/null; then
    # enkel het template committen, niets van het werk in uitvoering
    git -C "$MAP" add -- "$TEMPLATE" >> "$LOG" 2>&1
    git -C "$MAP" commit -qm "HEMS-template bijgewerkt naar $tag" -- "$TEMPLATE" >> "$LOG" 2>&1 || true
fi

# ---- 8. knop met een terminal waarin de agent start --------------------------------------------

stap "Knop \"HEMS bouwen met AI\" maken"
term=""
for t in gnome-terminal kgx ptyxis konsole xfce4-terminal mate-terminal x-terminal-emulator xterm; do
    command -v "$t" >/dev/null 2>&1 && { term="$t"; break; }
done
mkdir -p "$HULPMAP" "$APPS"
{
    printf "MAP='%s'\nAGENT='%s'\nAGENT_PAD='%s'\nTEMPLATE='%s'\nMEETSYSTEEM='%s'\n" "$MAP" "$AGENT" "$AGENT_PAD" "$TEMPLATE" "$meetsysteem"
} > "$HULPMAP/instellingen"

cat > "$HULPMAP/start.sh" <<'START'
#!/usr/bin/env bash
# Gestart door de knop "HEMS bouwen met AI": opent de AI-agent in de projectmap.
export PATH="$HOME/.local/bin:$PATH"
. "$HOME/.local/share/hems-ai/instellingen"
cd "$MAP" || { echo "De projectmap $MAP bestaat niet meer."; read -r -p "Enter om te sluiten "; exit 1; }
printf '\033]0;HEMS bouwen met AI\007'
clear
cat <<UITLEG
==============================================================
 HEMS bouwen met AI   (map: $MAP)
==============================================================
 - Typ gewoon in je eigen woorden wat je wil. De AI vraagt je
   toestemming voor commando's: lees ze, keur niet blind goed.
 - Vraagt de AI een commando met sudo (wachtwoord): voer dat
   zelf uit. Geef je wachtwoord nooit in het gesprek.
 - Stoppen kan altijd: typ /exit (of twee keer Ctrl+C).
   Volgende keer klik je gewoon opnieuw op de knop.
==============================================================

UITLEG
if [ -f AGENTS.md ]; then
    vraag="Lees AGENTS.md (en installatie.md als die er is). Vat in een paar zinnen samen waar we gebleven waren en vraag wat ik nu wil doen."
else
    vraag="Lees $TEMPLATE en volg het."
    if [ -n "$MEETSYSTEEM" ] && [ -f "$MEETSYSTEEM/mijn-installatie.md" ]; then
        vraag="$vraag Lees in fase 1 eerst $MEETSYSTEEM/mijn-installatie.md (ingevuld met de vragenlijst van het HEMS-meetsysteem)."
    elif [ -n "$MEETSYSTEEM" ]; then
        vraag="$vraag Het HEMS-meetsysteem staat in $MEETSYSTEEM."
    fi
fi
A="${AGENT_PAD:-$AGENT}"
[ -x "$A" ] || A="$AGENT"
# de map van het meetsysteem mag de agent lezen zonder telkens te vragen (wijzigen vraagt nog altijd toestemming)
extra=(); [ -n "$MEETSYSTEEM" ] && [ -d "$MEETSYSTEEM" ] && extra=(--add-dir "$MEETSYSTEEM")
case "$AGENT" in
    claude) "$A" "$vraag" "${extra[@]}" ;;     # --add-dir neemt meerdere mappen: de vraag eerst
    codex)  "$A" "$vraag" ;;
    gemini) "$A" -i "$vraag" ;;
    kimi)
        # Kimi kan de sessie niet met een eerste vraag starten: toon wat je moet typen
        cat <<KIMI
 Zo begin je met Kimi Code:
  1. De eerste keer: typ  /login  en meld je aan
     (Kimi Code-abonnement of een API-sleutel van platform.kimi.com).
  2. Typ dan (of kopieer met Ctrl+Shift+C / plak met Ctrl+Shift+V):

     $vraag

KIMI
        "$A" "${extra[@]}" ;;
esac
echo
read -r -p "Het gesprek is gestopt. Druk op Enter om dit venster te sluiten. "
START
chmod 755 "$HULPMAP/start.sh"

case "$term" in
    gnome-terminal) exec_regel="gnome-terminal --title=\"HEMS bouwen met AI\" -- bash \"$HULPMAP/start.sh\"" ;;
    kgx|ptyxis)     exec_regel="$term -- bash \"$HULPMAP/start.sh\"" ;;
    konsole|xfce4-terminal|mate-terminal|x-terminal-emulator|xterm) exec_regel="$term -e bash \"$HULPMAP/start.sh\"" ;;
    *)              exec_regel="" ;;
esac
icoon="utilities-terminal"
for i in "${meetsysteem:-/nonexistent}/icoon/hems.png" "$HOME/.local/share/icons/hicolor/256x256/apps/hems-systeem.png" "$HOME/.local/share/icons/hicolor/128x128/apps/hems-systeem.png" \
         /usr/share/icons/hicolor/*/apps/hems-systeem.png "$HOME"/.local/share/icons/hicolor/*/apps/hems-systeem.png; do
    if [ -f "$i" ]; then case "$i" in */icoon/hems.png) icoon="$i" ;; *) icoon="hems-systeem" ;; esac; break; fi
done
WAAR=""
if [ -n "$exec_regel" ]; then
    cat > "$APPS/$DESKTOP_NAAM" <<DESK
[Desktop Entry]
Type=Application
Name=HEMS bouwen met AI
Comment=Je energiesysteem verder bouwen met $AGENT_NAAM
Exec=$exec_regel
Icon=$icoon
Terminal=false
Categories=Office;
DESK
    chmod 644 "$APPS/$DESKTOP_NAAM"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
    if dock_toevoegen; then WAAR="in het dock en in het menu"; else WAAR="in het menu"; fi
fi
voortgang_stop
log "klaar: term=$term waar=$WAAR"

# ---- 9. klaar --------------------------------------------------------------------------------

if [ "$AGENT" = kimi ]; then
    login_tekst="De eerste keer typ je in Kimi Code <tt>/login</tt> en meld je je aan. Het venster toont ook welke zin je daarna typt om te beginnen."
else
    login_tekst="De eerste keer vraagt $AGENT_NAAM je om aan te melden: kies de manier die bij je abonnement past, je browser opent en je meldt je daar aan."
fi
if [ -z "$exec_regel" ]; then
    melding "Alles staat klaar in $MAP, maar er is geen terminalprogramma gevonden voor een knop.\n\nStart de AI zo: open een terminal en typ\n<tt>bash $HULPMAP/start.sh</tt>\n\n$login_tekst"
    exit 0
fi
if [ "$OPNIEUW" = 1 ]; then
    tekst="Bijgewerkt: template $tag, knop <b>HEMS bouwen met AI</b> $WAAR.\n\nKlik op de knop om verder te werken. De AI leest zijn notities en zegt waar jullie gebleven waren."
else
    tekst="Alles staat klaar.\n\n• Projectmap: $MAP\n• AI: $AGENT_NAAM\n• Template: $tag\n• Knop <b>HEMS bouwen met AI</b> $WAAR\n\n$login_tekst\n\nDaarna stelt de AI je vragen over je installatie en wensen. Dat gesprek mag over meerdere dagen lopen: klik later gewoon opnieuw op de knop."
fi
if [ "$UI" = gui ]; then
    if zenity --question --title="$TITEL" --width=560 --text="$tekst" --ok-label="Nu starten" --cancel-label="Later" 2>/dev/null; then
        setsid bash -c "$exec_regel" >/dev/null 2>&1 < /dev/null &
    fi
else
    printf '\n%b\n\nStarten: klik op de knop, of typ  bash %s/start.sh\n' "$tekst" "$HULPMAP" | sed 's/<[^>]*>//g'
fi
exit 0
