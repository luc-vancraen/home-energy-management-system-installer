#!/usr/bin/env bash
# Verwijdert het HEMS-meetsysteem: services en timer stoppen en weghalen.
# Zonder opties blijven de projectmap, de database en de configuratie behouden.
#   bash verwijder.sh           services verwijderen, data behouden
#   bash verwijder.sh --alles   ook de projectmap met alle data verwijderen
set -euo pipefail

MAP="$(cd "$(dirname "$0")" && pwd)"
ALLES=0
[ "${1:-}" = "--alles" ] && ALLES=1

echo "Services stoppen en uitschakelen ..."
# Enkel units die bij DEZE projectmap horen (ExecStart wijst naar $MAP/app/): hems-systeem-* (sinds 1.1) en de
# oude namen ems-* (versie 1.0). Een ems-* van een ander systeem blijft staan.
weg=""
for basis in hems-systeem-meter hems-systeem-dashboard hems-systeem-prijzen hems-systeem-rapport \
             ems-meter ems-dashboard ems-prijzen ems-rapport; do
    f="/etc/systemd/system/$basis.service"
    if [ -f "$f" ] && grep -q "^ExecStart=.* $MAP/app/" "$f"; then
        weg="$weg $basis.service"
        [ -f "/etc/systemd/system/$basis.timer" ] && weg="$weg $basis.timer"
    fi
done
if [ -n "$weg" ]; then
    sudo systemctl disable --now $weg 2>/dev/null || true
    for u in $weg; do sudo rm -f "/etc/systemd/system/$u"; done
    sudo systemctl daemon-reload
fi
echo "Services verwijderd."

bureaublad=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    bureaublad=$(xdg-user-dir DESKTOP 2>/dev/null || true)
fi
for map in "$bureaublad" "$HOME/Desktop" "$HOME/Bureaublad" "$HOME/.local/share/applications"; do
    [ -n "$map" ] && rm -f "$map/hems-dashboard.desktop"
done
if command -v gsettings >/dev/null 2>&1 && gsettings list-schemas 2>/dev/null | grep -qx org.gnome.shell; then
    fav=$(gsettings get org.gnome.shell favorite-apps 2>/dev/null || echo "")
    case "$fav" in
        *hems-dashboard.desktop*)
            nieuw=$(printf '%s' "$fav" | sed -e "s/, *'hems-dashboard.desktop'//; s/'hems-dashboard.desktop', *//; s/'hems-dashboard.desktop'//")
            gsettings set org.gnome.shell favorite-apps "$nieuw" 2>/dev/null || true ;;
    esac
fi
echo "Snelkoppelingen verwijderd."

if [ "$ALLES" -eq 1 ]; then
    read -r -p "Ook de hele map $MAP met alle meetdata verwijderen? Typ 'ja' om te bevestigen: " antw
    if [ "$antw" = "ja" ]; then
        rm -rf "$MAP"
        echo "Map $MAP verwijderd."
    else
        echo "Map behouden."
    fi
else
    echo "De projectmap $MAP (met je data en configuratie) blijft bestaan."
fi
echo "Klaar."
