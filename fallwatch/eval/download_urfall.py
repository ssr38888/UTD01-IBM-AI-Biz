"""Download UR Fall Detection Dataset camera-0 RGB sequences (30 falls, 40 everyday activities).

License: CC BY-NC-SA 4.0, non-commercial academic use. Cite Kwolek & Kępski (2014),
Computer Methods and Programs in Biomedicine. http://fenix.ur.edu.pl/~mkepski/ds/uf.html

    python eval/download_urfall.py [out_dir] [--small]   # --small = 5 falls + 5 ADL
"""
import sys
import urllib.request
from pathlib import Path

BASE = "http://fenix.ur.edu.pl/~mkepski/ds/data"

out = Path(next((a for a in sys.argv[1:] if not a.startswith("--")), "data/urfall"))
small = "--small" in sys.argv
out.mkdir(parents=True, exist_ok=True)
names = [f"fall-{i:02d}" for i in range(1, (6 if small else 31))] + \
        [f"adl-{i:02d}" for i in range(1, (6 if small else 41))]
for n in names:
    dest = out / f"{n}-cam0-rgb.zip"
    if dest.exists():
        continue
    print("downloading", dest.name)
    urllib.request.urlretrieve(f"{BASE}/{n}-cam0-rgb.zip", dest)
print("done:", out)
