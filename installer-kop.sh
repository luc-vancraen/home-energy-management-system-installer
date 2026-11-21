#!/usr/bin/env bash
#
# hems-systeem-install.sh — installeert het HEMS-meetsysteem op een Linux-pc
# ============================================================================
#
# Eén zelfstandig bestand dat het volledige meetsysteem bevat (meter uitlezen,
# opslag in SQLite, webdashboard, dagrapporten, marktprijzen) en dat:
#   1. de afhankelijkheden controleert en ontbrekende pakketten installeert
#      (python3, tzdata — meer is niet nodig: het systeem draait op standaard
#      Python 3, zonder pip-pakketten),
#   2. op een pc met bureaublad met vensters werkt (zenity), in een terminal met menu's
#      (whiptail), en anders of met opties met gewone tekstvragen,
#   3. alle bestanden plaatst (met sha256-integriteitscontrole),
#   4. systemd-services installeert en start,
#   5. meteen een eerste meting toont.
#
# Het systeem MEET en RAPPORTEERT alleen; het schrijft nooit naar een toestel.
# Na de installatie is geen AI-platform meer nodig; alles draait lokaal.
#
# Gebruik:  bash hems-systeem-install.sh [opties]   (zie --help)
# Verwijderen: bash <projectmap>/verwijder.sh
#
# Gebouwd volgens hems-template-nl.md (CC BY-SA 4.0, luc-vancraen).
# Software: MIT-licentie, Copyright (c) 2026 luc-vancraen.
# https://github.com/luc-vancraen/home-energy-management-system-installer

if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi
set -euo pipefail

VERSIE="1.4.1"
STANDAARD_DIR="$HOME/hems-systeem"
ENERGYZERO="https://api.energyzero.nl/v1/energyprices"

BESTANDEN=(
    app/ems_common.py
    app/ems_meterd.py
    app/ems_prijzen.py
    app/ems_dashboard.py
    app/ems_rapport.py
    app/ems_instellingen.py
    app/ems_kosten.py
    app/ems_netgebieden.py
    app/ems_vragenlijst.py
    app/ems_tablet.py
    config/ems.ini.example
    icoon/hems.png
    LEESMIJ.md
    verwijder.sh
)
SHA=("368c4bf8666772c29a2113bfe32d16180441cc2c4d7271f087570f365daa6649" "9b7ff85b1c67353f618747736416ec1a382c121fc14706d6ba078ff55bf94565" "c9c0ab311b44ca07d75c0bbd2931bb6cd5651ec0f14f232c72dcd9f85c6c9557" "033f3830d850a699cbd62fdfd52d65501717e450fee5b0a0ff584f1f7adfddc2" "f71402c30ec7d632d213054ae5f56faa63c6f56f27eabc308194bead72f15f38" "d20b934545acb2bad9b174b0d9ff427ec6d7982d265ed8b76ce497aa17d26978" "010c710a8708d28da0049a3556adffa02f4140214c066ffbced6216833649368" "c1d8bcc557486eaf5862c75f3972fb58018785dd7b1d629eca02a28031c21a4e" "6feebf16b613893a4f1afd11819b9dcfcf7ae8f5058e532b40e7c165cc8401a2")

# ---- opties -----------------------------------------------------------------

DEST=""
METER_URL=""
PROVIDER_URL=""
PROVIDER_TOKEN=""
POORT=""
DEMO=0
GEEN_SERVICES=0
GEEN_SNELKOPPELING=0
FORCE=0
SNELKOPPELING_OK=0
UI=auto                 # gui (zenity) | menu (whiptail) | tekst; auto = zelf kiezen
OPTIES_GEGEVEN=0        # opties op de opdrachtregel => gewone tekst (scripts, containers)
LOG=""

# ---- hulpfuncties -----------------------------------------------------------

if [ -t 1 ]; then
    C_INFO=$'\033[1;34m'; C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[1;31m'; C_OFF=$'\033[0m'
else
    C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""; C_OFF=""
fi
info() { printf '%s==>%s %s\n' "$C_INFO" "$C_OFF" "$*"; }
ok()   { printf '%s OK%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%sLET OP%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die()  { printf '%sFOUT%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

usage() {
    echo "hems-systeem-install.sh versie $VERSIE"
    cat <<'EOF'
Gebruik:
  bash hems-systeem-install.sh                      interactieve installatie in ~/hems-systeem
  bash hems-systeem-install.sh [opties]

Opties:
  --dir MAP              projectmap (standaard ~/hems-systeem)
  --meter-url URL        lokale API van de meter-dongle, bv. http://192.168.1.50/api/v1/data
  --provider-url URL     provider-API voor marktprijzen ('geen' = uit)
  --provider-token TKN   token voor de provider-API (komt in .env, nooit in de config)
  --poort N              dashboardpoort (standaard 8080)
  --demo                 gesimuleerde metingen (geen meter nodig, om te verkennen)
  --geen-services        geen systemd-services installeren (handmatig starten)
  --geen-snelkoppeling   geen snelkoppeling (bureaublad, dock, menu) plaatsen
  --force                installeren in een niet-lege map (back-up van gewijzigde bestanden)
  --gui                  altijd met vensters (zenity)
  --menu                 altijd met menu's in de terminal (whiptail)
  --tekst                altijd met tekstvragen in de terminal
  --versie               versienummer tonen
  --help                 deze hulp

Zonder opties kiest de installer zelf: vensters op een pc met bureaublad, menu's in een
terminal, anders tekstvragen. Met vensters of menu's vraagt hij bijna niets: de meter,
het contract en de prijzen stel je daarna in op de pagina Instellingen van het dashboard.
EOF
    exit 0
}

extract() {
    # $1 = payloadmarkering, $2 = doelbestand
    awk -v marker="$1" '
        $0 == marker { gevonden=1; next }
        gevonden && /^__[A-Z0-9_]+__$/ { exit }
        gevonden { print }
    ' "$0" | base64 -d > "$2"
}

vraag() {
    # $1 = vraag, $2 = standaardantwoord; antwoord op stdout
    local antw
    if [ -t 0 ]; then
        read -r -p "$1" antw </dev/tty || antw=""
    fi
    printf '%s' "${antw:-$2}"
}

vraag_geheim() {
    # zoals vraag, maar zonder het antwoord op het scherm te tonen (tokens)
    local antw=""
    if [ -t 0 ]; then
        read -r -s -p "$1" antw </dev/tty || antw=""
        echo >&2
    fi
    printf '%s' "$antw"
}

ini_waarde() {
    # $1 = sectie, $2 = sleutel; waarde uit $CONFIG op stdout (leeg als afwezig)
    [ -f "$CONFIG" ] || return 0
    awk -v s="[$1]" -v k="$2" '
        $0 == s { binnen=1; next }
        /^\[/ { binnen=0 }
        binnen && $0 ~ "^" k "[ \t]*=" { sub(/^[^=]*=[ \t]*/, ""); sub(/[ \t]+$/, ""); print; exit }
    ' "$CONFIG"
}

zet_ini() {
    # $1 = sectie, $2 = sleutel, $3 = waarde; past de regel aan binnen die sectie
    local w
    w=$(printf '%s' "$3" | sed -e 's/[\\|&]/\\&/g')
    [ -n "$w" ] && w=" $w"
    sed -i -e "/^\[$1\]/,/^\[/ s|^$2 *=.*|$2 =$w|" "$CONFIG"
}

# ---- argumenten -------------------------------------------------------------

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)             [ $# -ge 2 ] || die "--dir vereist een map"; DEST="$2"; shift 2 ;;
        --dir=*)           DEST="${1#--dir=}"; shift ;;
        --meter-url)       [ $# -ge 2 ] || die "--meter-url vereist een URL"; METER_URL="$2"; shift 2; OPTIES_GEGEVEN=1 ;;
        --meter-url=*)     METER_URL="${1#--meter-url=}"; shift; OPTIES_GEGEVEN=1 ;;
        --provider-url)    [ $# -ge 2 ] || die "--provider-url vereist een URL"; PROVIDER_URL="$2"; shift 2; OPTIES_GEGEVEN=1 ;;
        --provider-url=*)  PROVIDER_URL="${1#--provider-url=}"; shift; OPTIES_GEGEVEN=1 ;;
        --provider-token)  [ $# -ge 2 ] || die "--provider-token vereist een waarde"; PROVIDER_TOKEN="$2"; shift 2; OPTIES_GEGEVEN=1 ;;
        --provider-token=*) PROVIDER_TOKEN="${1#--provider-token=}"; shift; OPTIES_GEGEVEN=1 ;;
        --poort)           [ $# -ge 2 ] || die "--poort vereist een getal"; POORT="$2"; shift 2; OPTIES_GEGEVEN=1 ;;
        --poort=*)         POORT="${1#--poort=}"; shift; OPTIES_GEGEVEN=1 ;;
        --demo)            DEMO=1; OPTIES_GEGEVEN=1; shift ;;
        --geen-services)   GEEN_SERVICES=1; OPTIES_GEGEVEN=1; shift ;;
        --gui)             UI=gui; shift ;;
        --menu)            UI=menu; shift ;;
        --tekst)           UI=tekst; shift ;;
        --geen-snelkoppeling) GEEN_SNELKOPPELING=1; shift ;;
        --force|-f)        FORCE=1; shift ;;
        --versie|--version) echo "$VERSIE"; exit 0 ;;
        --help|-h)         usage ;;
        *)                 die "Onbekende optie: $1 (probeer --help)" ;;
    esac
done

# ---- uiterlijk: vensters, menu's of tekst ------------------------------------

if [ "$UI" = auto ]; then
    if [ "$OPTIES_GEGEVEN" -eq 1 ]; then
        UI=tekst
    elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null 2>&1; then
        UI=gui
    elif [ -t 0 ] && [ -t 1 ] && command -v whiptail >/dev/null 2>&1; then
        UI=menu
    else
        UI=tekst
    fi
fi
if [ "$UI" = menu ] && ! command -v whiptail >/dev/null 2>&1; then
    echo "whiptail ontbreekt: verder met tekst." >&2
    UI=tekst
fi
if [ "$UI" = gui ] && { [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || ! command -v zenity >/dev/null 2>&1; }; then
    echo "Vensters zijn hier niet mogelijk (geen bureaublad of geen zenity): verder met tekst." >&2
    UI=tekst
fi
TITEL="HEMS-meetsysteem $VERSIE"

ui_ja() {
    # $1 = tekst, $2 = knop ja, $3 = knop nee; succes = ja
    case "$UI" in
        gui)  zenity --question --title="$TITEL" --width=480 --text="$1" --ok-label="$2" --cancel-label="$3" 2>/dev/null ;;
        menu) whiptail --title "$TITEL" --yes-button "$2" --no-button "$3" --yesno "$1" 16 74 ;;
        *)    local a; a=$(vraag "$1 [j/N] " "n"); [[ "$a" =~ ^[jJyY] ]] ;;
    esac
}
ui_melding() {
    case "$UI" in
        gui)  zenity --info --title="$TITEL" --width=520 --text="$1" 2>/dev/null || true ;;
        menu) whiptail --title "$TITEL" --msgbox "$1" 20 76 || true ;;
        *)    printf '%s\n' "$1" ;;
    esac
}

if [ "$UI" = gui ]; then
    # Details naar een logbestand; in beeld enkel een voortgangsvenster en de vragen.
    LOG="$HOME/hems-systeem-installatie.log"
    : > "$LOG"
    start_voortgang() {
        exec 3> >(zenity --progress --pulsate --auto-close --no-cancel --title="$TITEL" --width=480 \
                         --text="Installeren ..." 2>/dev/null || true)
    }
    exec >>"$LOG" 2>&1
    info() { printf '==> %s\n' "$*"; printf '# %s\n' "$*" >&3 2>/dev/null || true; }
    ok()   { printf ' OK %s\n' "$*"; }
    warn() { printf 'LET OP %s\n' "$*"; }
    die()  {
        printf 'FOUT %s\n' "$*"
        printf '100\n' >&3 2>/dev/null || true; exec 3>&- || true
        zenity --error --title="$TITEL" --width=520 \
               --text="De installatie is gestopt:\n\n$*\n\nDetails staan in $LOG" 2>/dev/null || true
        exit 1
    }
    trap 'die "Onverwachte fout (regel $LINENO). Er is niets kapot: je kan de installatie opnieuw starten."' ERR
    # sudo vraagt zijn wachtwoord in een venster i.p.v. in een terminal
    ASKPASS=$(mktemp "${TMPDIR:-/tmp}/hems-askpass.XXXXXX")
    cat > "$ASKPASS" <<'ASK'
#!/bin/sh
exec zenity --password --title="HEMS-meetsysteem: wachtwoord nodig" 2>/dev/null
ASK
    chmod 700 "$ASKPASS"
    export SUDO_ASKPASS="$ASKPASS"
    sudo() { command sudo -A "$@"; }
    trap 'rm -f "$ASKPASS"' EXIT
elif [ "$UI" = menu ]; then
    die() {
        printf '%sFOUT%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2
        whiptail --title "$TITEL" --msgbox "De installatie is gestopt:\n\n$*" 16 76 || true
        exit 1
    }
fi

if [ "$UI" != tekst ]; then
    DEST="${DEST:-$STANDAARD_DIR}"
    if [ -f "$DEST/config/ems.ini" ]; then
        ui_ja "Er staat al een HEMS-meetsysteem in $DEST.\n\nBijwerken naar deze versie? Je instellingen en metingen blijven bewaard." \
              "Bijwerken" "Annuleren" || exit 0
        FORCE=1
    elif [ -d "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
        die "De map $DEST bestaat al en is niet leeg, maar er staat geen HEMS-meetsysteem in. Verplaats of hernoem die map en start opnieuw."
    else
        ui_ja "Dit installeert het HEMS-meetsysteem in $DEST.\n\nHet leest je digitale meter uit en toont je verbruik en kosten in een dashboard. Het stuurt geen enkel toestel aan.\n\nTijdens de installatie vraagt je computer misschien je wachtwoord." \
              "Installeren" "Annuleren" || exit 0
    fi
    [ "$UI" = gui ] && start_voortgang
fi

echo "============================================================"
echo " HEMS-meetsysteem — installatie"
echo " (alleen meten en rapporteren; stuurt geen toestellen)"
echo "============================================================"

# ---- stap 1: afhankelijkheden controleren en installeren --------------------

info "Stap 1/7: afhankelijkheden controleren ..."
for tool in awk base64 sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || die "Ontbrekend hulpprogramma: $tool (pakket coreutils)"
done

python_ok() {
    command -v python3 >/dev/null 2>&1 && python3 - <<'EOF' >/dev/null 2>&1
import sys, sqlite3
from zoneinfo import ZoneInfo
ZoneInfo("Europe/Brussels")
assert sys.version_info >= (3, 9)
EOF
}

if ! python_ok; then
    warn "python3 (>= 3.9, met sqlite3 en tzdata) ontbreekt of is onvolledig."
    if command -v apt-get >/dev/null 2>&1; then
        echo "  Installeren met: sudo apt-get update && sudo apt-get install -y python3 tzdata"
        sudo apt-get update && sudo apt-get install -y python3 tzdata
    fi
    python_ok || die "python3 >= 3.9 met sqlite3 en tzdata is vereist. Installeer het met je pakketbeheerder en probeer opnieuw."
fi
ok "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') met sqlite3 en tzdata"

if [ "$GEEN_SERVICES" -eq 0 ]; then
    if [ ! -d /run/systemd/system ]; then
        warn "systemd draait niet (bv. WSL zonder systemd): services worden overgeslagen."
        GEEN_SERVICES=1
    elif ! command -v sudo >/dev/null 2>&1; then
        warn "sudo ontbreekt: services worden overgeslagen. Start de programma's handmatig (zie LEESMIJ.md)."
        GEEN_SERVICES=1
    fi
fi

# ---- stap 2: vragen (meter-API, provider-API, map, poort) -------------------

info "Stap 2/7: configuratie opvragen ..."
[ -n "$DEST" ] || DEST=$(vraag "Projectmap [$STANDAARD_DIR]: " "$STANDAARD_DIR")
CONFIG="$DEST/config/ems.ini"
# Bij herinstallatie: wat al in de config staat blijft gelden, opties overschrijven het.
HUIDIG_METER=$(ini_waarde meter url)
HUIDIG_PROVIDER=$(ini_waarde provider api_url)
HUIDIG_POORT=$(ini_waarde dashboard poort)
HUIDIG_TOKEN=0
grep -q '^HEMS_PROVIDER_TOKEN=.' "$DEST/.env" 2>/dev/null && HUIDIG_TOKEN=1
[ -f "$CONFIG" ] && info "Bestaande configuratie gevonden: opgegeven opties werken die bij, de rest blijft."

if [ "$UI" = tekst ] && [ "$DEMO" -eq 0 ] && [ -z "$METER_URL" ] && [ -z "$HUIDIG_METER" ]; then
    echo "  De meter-API is de lokale URL van je uitleesdongle."
    echo "  HomeWizard P1: http://<ip-van-de-dongle>/api/v1/data  (API v1 aanzetten in de app)"
    METER_URL=$(vraag "  URL van de meter-API (leeg = demo-modus): " "")
fi
if [ -z "$METER_URL" ] && [ -z "$HUIDIG_METER" ]; then
    DEMO=1
    warn "Geen meter-API opgegeven: DEMO-modus met gesimuleerde metingen."
    warn "De meter kies je later op de pagina Instellingen van het dashboard (Zoek mijn meter)."
elif [ -n "$METER_URL" ] && [[ ! "$METER_URL" =~ ^https?:// ]]; then
    die "De meter-URL moet met http:// of https:// beginnen."
fi

if [ "$UI" = tekst ] && [ -z "$PROVIDER_URL" ] && [ -z "$HUIDIG_PROVIDER" ]; then
    echo "  De provider-API levert de day-ahead-marktprijzen (optioneel)."
    echo "  Voorbeeld zonder token: $ENERGYZERO"
    echo "  ENTSO-E: https://web-api.tp.entsoe.eu/api (gratis token nodig)"
    PROVIDER_URL=$(vraag "  Provider-API URL (leeg = geen prijzen): " "")
fi
PROVIDER_UIT=0
[ "$PROVIDER_URL" = "geen" ] && { PROVIDER_URL=""; PROVIDER_UIT=1; }
if [ -n "${PROVIDER_URL:-$HUIDIG_PROVIDER}" ] && [ "$PROVIDER_UIT" -eq 0 ] \
   && [ -z "$PROVIDER_TOKEN" ] && [ "$HUIDIG_TOKEN" -eq 0 ]; then
    PROVIDER_TOKEN=$(vraag_geheim "  Provider-API token (wordt niet getoond; leeg = niet nodig): ")
fi

vrije_poort() {
    # eerste vrije poort vanaf 8080 (zodat het dashboard niet botst met iets dat al draait)
    python3 - <<'PY' 2>/dev/null || echo 8080
import socket
for p in list(range(8080, 8100)) + list(range(8180, 8200)):
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", p)); print(p); break
    except OSError:
        pass
    finally:
        s.close()
PY
}
[ -n "$POORT" ] || POORT="$HUIDIG_POORT"
if [ -z "$POORT" ] && [ "$UI" != tekst ]; then
    POORT=$(vrije_poort)
fi
[ -n "$POORT" ] || POORT=$(vraag "Dashboardpoort [8080]: " "8080")
[[ "$POORT" =~ ^[0-9]+$ ]] && [ "$POORT" -ge 1 ] && [ "$POORT" -le 65535 ] || die "Ongeldige poort: $POORT"

# ---- stap 3: projectmap en bestanden ----------------------------------------

info "Stap 3/7: bestanden plaatsen in $DEST ..."
if [ -d "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ] && [ "$FORCE" -eq 0 ]; then
    die "De map $DEST bestaat en is niet leeg. Kies een andere map of gebruik --force (gewijzigde bestanden krijgen eerst een back-up)."
fi
mkdir -p "$DEST"

# Gewijzigde programmabestanden gaan bij een update naar backup/JJJJMMDD-UUMMSS/ (niet meer als .bak naast
# de programma's); enkel de laatste 5 updates blijven bewaard.
BACKUP_MAP="$DEST/backup/$(date +%Y%m%d-%H%M%S)"
i=0
while [ "$i" -lt "${#BESTANDEN[@]}" ]; do
    rel="${BESTANDEN[$i]}"
    doel="$DEST/$rel"
    mkdir -p "$(dirname "$doel")"
    if [ -f "$doel" ] && ! echo "${SHA[$i]}  $doel" | sha256sum -c - >/dev/null 2>&1; then
        mkdir -p "$BACKUP_MAP/$(dirname "$rel")"
        cp -p "$doel" "$BACKUP_MAP/$rel"
        info "Vorige versie van $rel bewaard in ${BACKUP_MAP#"$DEST"/}/"
    fi
    extract "__HEMS_BESTAND_${i}__" "$doel"
    echo "${SHA[$i]}  $doel" | sha256sum -c - >/dev/null 2>&1 \
        || die "Integriteitscontrole mislukt voor $rel; dit installerbestand is beschadigd."
    ok "$rel"
    i=$((i + 1))
done
chmod +x "$DEST/verwijder.sh"
echo "$VERSIE" > "$DEST/VERSIE"           # het dashboard toont dit en vergelijkt het met de laatste release
# Oude .bak-bestanden van vroegere versies (naast de programma's) mee naar backup/ verhuizen.
for oudbak in "$DEST"/app/*.bak.* "$DEST"/*.md.bak.* "$DEST"/verwijder.sh.bak.* "$DEST"/icoon/*.bak.* \
              "$DEST"/config/ems.ini.example.bak.*; do
    [ -f "$oudbak" ] || continue
    mkdir -p "$DEST/backup/oude-bak-bestanden"
    mv "$oudbak" "$DEST/backup/oude-bak-bestanden/"
done
if [ -d "$DEST/backup" ]; then
    aantal=$(ls -1d "$DEST"/backup/[0-9]*-[0-9]* 2>/dev/null | wc -l)
    ls -1d "$DEST"/backup/[0-9]*-[0-9]* 2>/dev/null | sort | awk -v n="$aantal" 'NR <= n - 5' \
        | while read -r oud; do rm -rf "$oud"; done
fi

# ---- stap 4: configuratie en .env -------------------------------------------

info "Stap 4/7: configuratie ..."
DEMOSTAND=auto
[ "$DEMO" -eq 1 ] && [ -n "$METER_URL" ] && DEMOSTAND=ja   # --demo met url: demo forceren
if [ -f "$CONFIG" ]; then
    WIJZIG=""
    [ -n "$METER_URL" ] && [ "$METER_URL" != "$HUIDIG_METER" ] && WIJZIG="$WIJZIG meter-URL"
    [ -n "$METER_URL" ] && [ "$DEMOSTAND" = "auto" ] && [ "$(ini_waarde meter demo)" = "ja" ] && WIJZIG="$WIJZIG demo"
    [ -n "$PROVIDER_URL" ] && [ "$PROVIDER_URL" != "$HUIDIG_PROVIDER" ] && WIJZIG="$WIJZIG provider-URL"
    [ "$PROVIDER_UIT" -eq 1 ] && [ -n "$HUIDIG_PROVIDER" ] && WIJZIG="$WIJZIG provider-uit"
    [ "$POORT" != "$HUIDIG_POORT" ] && WIJZIG="$WIJZIG poort"
    if [ -n "$WIJZIG" ]; then
        cp -p "$CONFIG" "$CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
        [ -n "$METER_URL" ] && zet_ini meter url "$METER_URL"
        [ -n "$METER_URL" ] && [ "$DEMOSTAND" = "auto" ] && sed -i -e "/^\[meter\]/,/^\[/ s|^demo *= *ja *\$|demo = auto|" "$CONFIG"
        [ -n "$PROVIDER_URL" ] && zet_ini provider api_url "$PROVIDER_URL"
        [ "$PROVIDER_UIT" -eq 1 ] && zet_ini provider api_url ""
        zet_ini dashboard poort "$POORT"
        ok "Bestaande configuratie bijgewerkt:$WIJZIG (back-up gemaakt)"
    else
        info "Bestaande configuratie behouden ($CONFIG)"
    fi
else
    cp "$DEST/config/ems.ini.example" "$CONFIG"
    zet_ini meter url "$METER_URL"
    zet_ini meter demo "$DEMOSTAND"
    zet_ini provider api_url "$PROVIDER_URL"
    zet_ini dashboard poort "$POORT"
    ok "Configuratie geschreven: $CONFIG"
fi
# Vanaf hier gelden de waarden zoals ze echt in de config staan.
METER_URL=$(ini_waarde meter url)
PROVIDER_URL=$(ini_waarde provider api_url)
POORT=$(ini_waarde dashboard poort)
DEMO=0
case "$(ini_waarde meter demo)" in
    ja) DEMO=1 ;;
    nee) ;;
    *) [ -z "$METER_URL" ] && DEMO=1 ;;
esac

if [ -n "$PROVIDER_TOKEN" ]; then
    ENV="$DEST/.env"
    (
        umask 077
        touch "$ENV"
        grep -v "^HEMS_PROVIDER_TOKEN=" "$ENV" > "$ENV.tmp" 2>/dev/null || true
        printf 'HEMS_PROVIDER_TOKEN=%s\n' "$PROVIDER_TOKEN" >> "$ENV.tmp"
        mv "$ENV.tmp" "$ENV"
    )
    chmod 600 "$ENV"
    ok "Token bewaard in $ENV (alleen leesbaar voor jou)"
elif [ -n "$PROVIDER_URL" ] && [[ "$PROVIDER_URL" == *entsoe* ]] && [ "$HUIDIG_TOKEN" -eq 0 ]; then
    warn "ENTSO-E heeft een token nodig: zet HEMS_PROVIDER_TOKEN=... in $DEST/.env (chmod 600)."
fi

# Eenmalige instelcode voor de pagina Instellingen, zolang er geen beheerwachtwoord is.
INSTELCODE=""
if ! grep -q '^HEMS_BEHEER_WACHTWOORD=.' "$DEST/.env" 2>/dev/null; then
    INSTELCODE=$(PYTHONPATH="$DEST/app" python3 -c 'import sys, ems_common as ec; print(ec.instelcode(sys.argv[1]))' "$CONFIG" 2>/dev/null || true)
fi

# ---- stap 5: systemd-services ------------------------------------------------

PYTHON_BIN=$(command -v python3)
installeer_unit() {
    # $1 = unit-naam, inhoud op stdin
    sudo tee "/etc/systemd/system/$1" >/dev/null
}

# Hoort een bestaande unit bij deze projectmap? (ExecStart wijst naar $DEST/app/)
unit_van_deze_map() {
    [ -f "/etc/systemd/system/$1" ] && grep -q "^ExecStart=.* $DEST/app/" "/etc/systemd/system/$1"
}
OUDE_UNITS=""
if [ "$GEEN_SERVICES" -eq 0 ]; then
    for u in ems-meter.service ems-dashboard.service ems-rapport.service ems-prijzen.service; do
        if unit_van_deze_map "$u"; then
            case "$u" in
                ems-meter.service|ems-dashboard.service) OUDE_UNITS="$OUDE_UNITS $u" ;;
                *) OUDE_UNITS="$OUDE_UNITS ${u%.service}.timer" ;;
            esac
        elif [ -f "/etc/systemd/system/$u" ]; then
            info "Er bestaat al een $u van een ander systeem: die blijft onaangeroerd (dit systeem gebruikt hems-systeem-*)."
        fi
    done
    OUDE_UNITS="${OUDE_UNITS# }"
    for u in hems-systeem-meter.service hems-systeem-dashboard.service; do
        if [ -f "/etc/systemd/system/$u" ] && ! unit_van_deze_map "$u"; then
            andere=$(sed -n 's|^ExecStart=[^ ]* \(.*\)/app/.*|\1|p' "/etc/systemd/system/$u" | head -n 1)
            die "Er is al een HEMS-meetsysteem geïnstalleerd in ${andere:-een andere map}. Werk dat bij (kies die map) of verwijder het eerst met: bash ${andere:-<die map>}/verwijder.sh"
        fi
    done
fi

if [ "$GEEN_SERVICES" -eq 0 ]; then
    info "Stap 5/7: systemd-services installeren ..."

    installeer_unit hems-systeem-meter.service <<EOF
[Unit]
Description=HEMS meterdaemon (digitale meter uitlezen)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
ExecStart=$PYTHON_BIN $DEST/app/ems_meterd.py --config $CONFIG
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    installeer_unit hems-systeem-dashboard.service <<EOF
[Unit]
Description=HEMS webdashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
ExecStart=$PYTHON_BIN $DEST/app/ems_dashboard.py --config $CONFIG
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    installeer_unit hems-systeem-rapport.service <<EOF
[Unit]
Description=HEMS dagrapport maken

[Service]
Type=oneshot
User=$USER
ExecStart=$PYTHON_BIN $DEST/app/ems_rapport.py --config $CONFIG
EOF

    installeer_unit hems-systeem-rapport.timer <<'EOF'
[Unit]
Description=HEMS dagrapport (elke ochtend over gisteren)

[Timer]
OnCalendar=*-*-* 06:05:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

    # De prijzentimer staat er altijd: zonder ingestelde bron doet hij niets. Zo werkt het ook als
    # iemand de marktprijzen later aanzet op de pagina Instellingen.
    installeer_unit hems-systeem-prijzen.service <<EOF
[Unit]
Description=HEMS marktprijzen ophalen
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
ExecStart=$PYTHON_BIN $DEST/app/ems_prijzen.py --config $CONFIG
EOF
    installeer_unit hems-systeem-prijzen.timer <<'EOF'
[Unit]
Description=HEMS marktprijzen ophalen (day-ahead komt rond 13 uur; extra pogingen voor als het mislukt)

[Timer]
OnCalendar=*-*-* 14:30:00
OnCalendar=*-*-* 17:30:00
OnCalendar=*-*-* 06:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
    UNITS="hems-systeem-meter.service hems-systeem-dashboard.service hems-systeem-rapport.timer hems-systeem-prijzen.timer"

    sudo systemctl daemon-reload
    sudo systemctl enable $UNITS
    # Omzetten vanaf versie 1.0 (services heetten ems-*): de oude units van DEZE installatie stoppen en
    # weghalen vóór de nieuwe starten (anders bezetten ze de poort). Units met die naam van een ander systeem
    # (niet in deze projectmap) blijven onaangeroerd.
    if [ -n "$OUDE_UNITS" ]; then
        sudo systemctl disable --now $OUDE_UNITS 2>/dev/null || true
        for u in $OUDE_UNITS; do
            sudo rm -f "/etc/systemd/system/$u"
            case "$u" in *.timer) sudo rm -f "/etc/systemd/system/${u%.timer}.service" ;; esac
        done
        sudo systemctl daemon-reload
        ok "Oude servicenamen (versie 1.0) omgezet naar hems-systeem-*: $OUDE_UNITS"
    fi
    sudo systemctl start $UNITS
    # start doet niets bij een service die al draait: herstarten zodat nieuwe code en config gelden
    sudo systemctl restart hems-systeem-meter.service hems-systeem-dashboard.service
    [ -n "$PROVIDER_URL" ] && { sudo systemctl start hems-systeem-prijzen.service || warn "Prijzen ophalen mislukt; zie: journalctl -u hems-systeem-prijzen"; }
    ok "Services actief: $UNITS"
else
    info "Stap 5/7: services overgeslagen (--geen-services)"
fi

# ---- stap 6: bureaubladsnelkoppeling ----------------------------------------

if [ "$GEEN_SNELKOPPELING" -eq 0 ]; then
    info "Stap 6/7: bureaubladsnelkoppeling ..."
    bureaublad=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        bureaublad=$(xdg-user-dir DESKTOP 2>/dev/null || true)
    fi
    if [ -z "$bureaublad" ] || [ "$bureaublad" = "$HOME" ] || [ ! -d "$bureaublad" ]; then
        bureaublad=""
        for kandidaat in "$HOME/Desktop" "$HOME/Bureaublad"; do
            if [ -d "$kandidaat" ]; then bureaublad="$kandidaat"; break; fi
        done
    fi
    if [ -n "$bureaublad" ] && [ -d "$bureaublad" ]; then
        snelkoppeling="$bureaublad/hems-dashboard.desktop"
        cat > "$snelkoppeling" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=HEMS Dashboard
Comment=Open het HEMS-energieoverzicht in de browser
Exec=xdg-open http://localhost:$POORT
Icon=$DEST/icoon/hems.png
Terminal=false
Categories=Office;
Keywords=energie;meter;hems;
EOF
        chmod 755 "$snelkoppeling"
        gio set "$snelkoppeling" metadata::trusted true 2>/dev/null || true
        mkdir -p "$HOME/.local/share/applications"
        cp "$snelkoppeling" "$HOME/.local/share/applications/hems-dashboard.desktop"
        SNELKOPPELING_OK=1
        waar="op het bureaublad en in het toepassingenmenu"
        # GNOME (o.a. Debian, Ubuntu, Fedora) toont geen pictogrammen op het bureaublad: zet het dashboard ook
        # in het dock (favorieten), anders vindt een gewone gebruiker het niet terug.
        if command -v gsettings >/dev/null 2>&1 \
           && gsettings list-schemas 2>/dev/null | grep -qx org.gnome.shell; then
            fav=$(gsettings get org.gnome.shell favorite-apps 2>/dev/null || echo "")
            case "$fav" in
                *hems-dashboard.desktop*) waar="in het dock en in het toepassingenmenu" ;;
                "["*"]")
                    if [ "$fav" = "[]" ] || [ "$fav" = "@as []" ]; then nieuw="['hems-dashboard.desktop']"
                    else nieuw="${fav%]}, 'hems-dashboard.desktop']"; fi
                    gsettings set org.gnome.shell favorite-apps "$nieuw" 2>/dev/null \
                        && waar="in het dock (onderaan, via Activiteiten) en in het toepassingenmenu" ;;
            esac
        fi
        ok "Snelkoppeling 'HEMS Dashboard' $waar"
    else
        info "Geen bureaubladmap gevonden (server zonder desktop?): snelkoppeling overgeslagen"
    fi
else
    info "Stap 6/7: snelkoppeling overgeslagen (--geen-snelkoppeling)"
fi

# ---- stap 7: eerste meting en samenvatting ----------------------------------

info "Stap 7/7: eerste test ..."
if [ "$DEMO" -eq 0 ]; then
    if ! "$PYTHON_BIN" "$DEST/app/ems_meterd.py" --config "$CONFIG" --eenmalig; then
        warn "De meter antwoordt (nog) niet. Controleer:"
        warn "- staat de lokale API aan (HomeWizard-app: Instellingen -> Meter -> API)?"
        warn "- klopt de URL en heeft de dongle een vast IP (DHCP-reservatie in je router)?"
        warn "Kies of test je meter op de pagina Instellingen van het dashboard (zie hieronder)."
    fi
else
    echo "Demo-modus: na een minuutje staat er gesimuleerde data op het dashboard."
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)   # BusyBox/Alpine kent geen -I
[ -n "$IP" ] || IP="localhost"

cat <<EOF

${C_OK}Installatie klaar.${C_OFF}

  Dashboard:     http://$IP:$POORT
  Instellingen:  http://$IP:$POORT/instellingen
  Tablet:        http://$IP:$POORT/tablet   (weergave voor een wandtablet)
$( [ -n "$INSTELCODE" ] && echo "  Instelcode:    $INSTELCODE   (eenmalig: hiermee kies je je beheerwachtwoord)" )
  Projectmap:    $DEST
  Configuratie:  $CONFIG
  Handleiding:   $DEST/LEESMIJ.md
$( [ "$SNELKOPPELING_OK" -eq 1 ] && echo "  Snelkoppeling:  'HEMS Dashboard' $waar" )
EOF
if [ "$GEEN_SERVICES" -eq 0 ]; then
    cat <<EOF

Handige commando's:
  journalctl -u hems-systeem-meter -f                               # live logboek van de metingen
  systemctl status hems-systeem-meter hems-systeem-dashboard        # status van de services
  bash $DEST/verwijder.sh                                           # alles stoppen en verwijderen
EOF
else
    cat <<EOF

Er zijn geen services geïnstalleerd (--geen-services of geen systemd): het systeem
start niet vanzelf. Starten met de hand, elk in een eigen terminal:
  cd $DEST && python3 app/ems_meterd.py --config $CONFIG
  cd $DEST && python3 app/ems_dashboard.py --config $CONFIG
Meer uitleg (ook marktprijzen en rapport): $DEST/LEESMIJ.md
EOF
fi
[ "$DEMO" -eq 1 ] && echo
[ "$DEMO" -eq 1 ] && cat <<EOF
Demo-modus is aan (het dashboard toont een rode DEMO-balk). Voor echte metingen:
open Instellingen (adres hierboven), klik op "Zoek mijn meter", dan "Test" en "Opslaan".
Herstarten is niet nodig. Demo-data staat in een aparte databank en komt niet tussen
je echte metingen.
EOF
echo

if [ "$UI" != tekst ]; then
    ADRES="http://localhost:$POORT/instellingen"
    [ -n "$INSTELCODE" ] && ADRES="$ADRES?code=$INSTELCODE"
    if [ "$GEEN_SERVICES" -eq 0 ]; then
        for _ in $(seq 1 20); do              # wachten tot het dashboard antwoordt (max. 10 s)
            python3 -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=1)" \
                "http://127.0.0.1:$POORT/" >/dev/null 2>&1 && break
            sleep 0.5
        done
    fi
    [ "$UI" = gui ] && { printf '100\n' >&3 2>/dev/null || true; exec 3>&- || true; trap - ERR; }
    if [ -n "$INSTELCODE" ]; then
        CODETEKST="Je instelcode: $INSTELCODE\n(eenmalig: hiermee kies je je beheerwachtwoord; ze staat ook in $DEST/config/instelcode)\n\n"
    else
        CODETEKST=""
    fi
    if [ "$GEEN_SERVICES" -eq 1 ]; then
        ui_melding "Het HEMS-meetsysteem is geïnstalleerd in $DEST, maar draait niet als dienst (zonder systemd of op jouw vraag), dus het start niet vanzelf.\n\n${CODETEKST}Start het zoals beschreven in $DEST/LEESMIJ.md."
    elif [ "$UI" = gui ]; then
        ui_melding "Het HEMS-meetsysteem is klaar.\n\n${CODETEKST}Klik op OK: je browser opent de pagina Instellingen. Daar zoek je je meter en vul je je contract in.\n\nLater open je het dashboard met de snelkoppeling 'HEMS Dashboard' of via http://$IP:$POORT"
        xdg-open "$ADRES" >/dev/null 2>&1 &
    else
        ui_melding "Het HEMS-meetsysteem is klaar.\n\n${CODETEKST}Open in je browser:\n  http://$IP:$POORT/instellingen\n\nDaar zoek je je meter en vul je je contract in."
    fi
fi

exit 0

# =============================================================================
# Ingebedde bestanden (base64). Niet wijzigen: sha256-controles hierboven
# bewaken de integriteit.
# =============================================================================

