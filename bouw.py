#!/usr/bin/env python3
"""Bouwt hems-systeem-install.sh uit installer-kop.sh + de bestanden in src/.

Gebruik: python3 bouw.py [uitvoerbestand]   (standaard: uit/hems-systeem-install.sh)
Het vrijgegeven script in ~/ wordt pas na testen met de hand vervangen.
"""
import base64, hashlib, re, sys
from pathlib import Path

HIER = Path(__file__).resolve().parent
kop = (HIER / "installer-kop.sh").read_text(encoding="utf-8")
m = re.search(r"^BESTANDEN=\(\n(.*?)\n\)", kop, re.S | re.M)
bestanden = [r.strip() for r in m.group(1).splitlines() if r.strip()]
data = [(HIER / "src" / b).read_bytes() for b in bestanden]
sha = " ".join(f'"{hashlib.sha256(d).hexdigest()}"' for d in data)
kop, n = re.subn(r"^SHA=\(.*\)$", f"SHA=({sha})", kop, flags=re.M)
assert n == 1, "SHA-regel niet gevonden"
delen = [kop]
for i, d in enumerate(data):
    delen.append(f"__HEMS_BESTAND_{i}__\n")
    delen.append(base64.encodebytes(d).decode("ascii") + "\n")
uit = Path(sys.argv[1]) if len(sys.argv) > 1 else HIER / "uit" / "hems-systeem-install.sh"
uit.parent.mkdir(exist_ok=True)
uit.write_text("".join(delen), encoding="utf-8")
uit.chmod(0o755)
print(f"{uit}  sha256 {hashlib.sha256(uit.read_bytes()).hexdigest()}")
