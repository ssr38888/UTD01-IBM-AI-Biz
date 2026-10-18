# Instructions for Codex (or any coding agent)

> **Before you hand this to an agent:** run `python3 scripts/make_keys.py` yourself in your own
> terminal. It prints your phone key and viewer key once and saves them to `.env`. Do this
> yourself so the keys never pass through the agent's chat or logs.
>
> Use an agent running **on your own laptop** (for example Codex CLI). An agent running in a cloud
> sandbox can't give you a link your phone can open, for the same reason Claude couldn't.

## Task

Run FallWatch locally and give me a public HTTPS link I can open on my phone.

1. Read `README.md` first.
2. Install the backend packages: `pip install -r backend/requirements.txt`. Use a virtualenv if one is active or preferred.
3. Check that `.env` exists at the repo root.
   - If it is missing, stop and tell me to run `python3 scripts/make_keys.py` myself.
   - Do NOT generate keys yourself. Do NOT open, print, cat, copy, log or commit `.env` or any key.
4. Start the server, bound to localhost only: `cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000`.
5. Check `http://127.0.0.1:8000/healthz` returns `{"ok": true}`.
6. Install `cloudflared` if it is missing (macOS: `brew install cloudflared`; Windows: `winget install Cloudflare.cloudflared`; Linux: the cloudflared package/binary from Cloudflare).
7. Run `cloudflared tunnel --url http://localhost:8000`. Find the `https://<random>.trycloudflare.com` address it prints.
8. Give me exactly two links:
   - Phone: `https://<random>.trycloudflare.com/capture/`
   - Viewer (laptop): `https://<random>.trycloudflare.com/viewer/`
   Remind me to type the phone key and viewer key from my own terminal output, not from you.

## Rules

- Never weaken security to make something work:
  - Don't add default or hard-coded keys.
  - Don't accept keys in URLs.
  - Don't remove the key check, the CSP or the HTTPS requirement.
  - Don't bind the server to 0.0.0.0.
- Never commit `.env`, `deploy/.env`, `data/`, or any key. `.gitignore` already covers them; keep it that way.
- If the phone page shows "Camera needs HTTPS", the link isn't HTTPS. Use the trycloudflare link, not a LAN IP.
- If something fails, show me the error and what you plan to change before changing application code.
- Run the tests after any code change:
  - `node --test tests/*.test.mjs`
  - `python3 -m pytest backend/tests`
