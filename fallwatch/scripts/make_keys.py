"""Create strong random keys for FallWatch and save them where only you can read them.

    python scripts/make_keys.py            # local run: writes .env at the repo root
    python scripts/make_keys.py --deploy   # LinuxONE: writes deploy/.env (adds DB password)
    python scripts/make_keys.py --force    # replace existing keys (old phones stop working)

The files are git-ignored and set to owner-only (chmod 600). The keys are printed once so you
can type the device key into the phone; nothing is sent anywhere.
"""
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def key() -> str:
    return secrets.token_urlsafe(32)  # 256 bits of randomness, URL/header safe


def main() -> int:
    deploy = "--deploy" in sys.argv
    force = "--force" in sys.argv
    target = ROOT / "deploy" / ".env" if deploy else ROOT / ".env"
    if target.exists() and not force:
        print(f"{target} already exists; keeping your keys. Use --force to replace them.")
        return 0

    phone, viewer = key(), key()
    values = {"DEVICE_KEYS": phone, "VIEWER_KEYS": viewer}
    if deploy:
        template = (ROOT / "deploy" / ".env.example").read_text().splitlines()
        values["POSTGRES_PASSWORD"] = key()
        out = []
        for line in template:
            name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
            out.append(f"{name}={values[name]}" if name in values else line)
        text = "\n".join(out) + "\n"
    else:
        text = ("# FallWatch secrets. Do not commit, paste into chats, or share screenshots of this file.\n"
                + "".join(f"{k}={v}\n" for k, v in values.items()))

    # Create with owner-only permissions from the start (no window where others can read it).
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass  # Windows: permissions are managed differently

    print(f"Saved to {target} (owner-only, git-ignored).\n")
    print(f"  Phone key  (type into /capture/):  {phone}")
    print(f"  Viewer key (type into /viewer/):   {viewer}\n")
    print("Keep these private. To revoke, run again with --force and restart the server.")
    if deploy:
        print("Also set SITE_ADDRESS in deploy/.env before `docker compose up -d --build`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
