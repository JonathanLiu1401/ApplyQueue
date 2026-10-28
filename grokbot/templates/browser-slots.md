# Per-worker browser profiles and debug ports

Each Grok Bot agent already has its own desktop and browser window on the shared box, and
browser logins on the box persist for every agent. Most teams need nothing more: sign each site in
once and let agents use their own browser windows.

Use this only if workers drive a **scripted Chromium** over the DevTools protocol (Playwright,
CDP). Then every worker needs its own profile directory and port, or two workers will fight over
one browser, one cookie jar and one set of tabs.

## Rules

- `browser/master` is signed in once (by the user, or by the captain with the user's logins from
  the vault). It is the template. **Never drive master from a worker.**
- Each worker gets `browser/worker-N`, seeded by copying master **while master is closed**, on
  port `9222 + N`. One agent per slot. A port already answering is never shared.
- Locks (`Singleton*`) and caches are not copied. Logins come along in the cookies.
- Profiles hold live sessions: keep them on the box only. Never commit, upload, zip or paste them,
  and never print cookie or session values.
- If a worker's session expires, sign that worker in again in its own slot (or re-seed it from a
  freshly signed-in master). Do not sign in to a second Google account in the same profile.
- The user's own existing browser profiles and saved passwords stay where they are; this script
  only creates new ones under `{{STATE_DIR}}/browser/`.
- Keep only the Gmail tab and the current application tab open; close the rest after each job.

## Usage

```bash
bin/browser-slot.sh master          # open master on port 9222, sign sites in, then close it
bin/browser-slot.sh worker-1        # seed (first time) and launch on 9223
bin/browser-slot.sh worker-2        # 9224
bin/browser-slot.sh list
bin/browser-slot.sh worker-1 stop
```

A worker connects with `http://127.0.0.1:<its port>` and never with another worker's port.
Record which agent uses which slot in AGENTS.md (`Apply 1 -> worker-1`, ...).

## browser-slot.sh

Save as `{{STATE_DIR}}/bin/browser-slot.sh` and `chmod +x` it.

```bash
#!/usr/bin/env bash
# browser-slot.sh: give one worker its own persistent Chromium profile and DevTools port on the box.
#
#   browser-slot.sh master          open the master profile (port 9222) so the user or captain signs sites in once
#   browser-slot.sh worker-1        seed browser/worker-1 from master (first run only) and launch on port 9223
#   browser-slot.sh worker-2 stop   stop that worker's browser
#   browser-slot.sh list            show running slots
#
# Ports: master = 9222, worker-N = 9222 + N. One agent per slot; never two agents on one port.
# Never drive master from a worker. Close master before seeding a worker (profiles copied while
# Chromium is running are corrupt). Profiles hold logins: they stay on the box and are never
# copied, committed or shared.
set -euo pipefail

STATE_DIR="${JOBQ_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BROWSERS="$STATE_DIR/browser"
SLOT="${1:?usage: browser-slot.sh master|worker-N|list [stop]}"
ACTION="${2:-start}"
mkdir -p "$BROWSERS"

if [[ "$SLOT" == "list" ]]; then
  for pidfile in "$BROWSERS"/*.pid; do
    [[ -e "$pidfile" ]] || continue
    name=$(basename "$pidfile" .pid); pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then echo "$name running pid=$pid port=$(cat "$BROWSERS/$name.port")"; else echo "$name stopped"; fi
  done
  exit 0
fi

if [[ "$SLOT" == "master" ]]; then
  PORT=9222
elif [[ "$SLOT" =~ ^worker-([0-9]+)$ ]]; then
  PORT=$((9222 + BASH_REMATCH[1]))
else
  echo "slot must be master or worker-N" >&2; exit 1
fi
PROFILE="$BROWSERS/$SLOT"
PIDFILE="$BROWSERS/$SLOT.pid"

if [[ "$ACTION" == "stop" ]]; then
  [[ -f "$PIDFILE" ]] && kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"; echo "stopped $SLOT"; exit 0
fi

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "$SLOT already running on port $PORT (pid $(cat "$PIDFILE"))"; exit 0
fi
if curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "port $PORT is already in use by another browser; refusing to share it" >&2; exit 1
fi

if [[ "$SLOT" != "master" && ! -d "$PROFILE" ]]; then
  if [[ ! -d "$BROWSERS/master" ]]; then
    echo "no master profile yet: run 'browser-slot.sh master' and sign sites in first" >&2; exit 1
  fi
  if [[ -f "$BROWSERS/master.pid" ]] && kill -0 "$(cat "$BROWSERS/master.pid")" 2>/dev/null; then
    echo "close the master browser before seeding $SLOT" >&2; exit 1
  fi
  mkdir -p "$PROFILE"
  # Copy the signed-in profile, minus locks and caches.
  tar -C "$BROWSERS/master" \
      --exclude='Singleton*' --exclude='*Cache*' --exclude='Crashpad' --exclude='*.log' \
      -cf - . | tar -C "$PROFILE" -xf -
  echo "seeded $SLOT from master"
fi

CHROME="$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)"
[[ -n "$CHROME" ]] || { echo "no Chromium/Chrome binary found" >&2; exit 1; }
rm -f "$PROFILE"/Singleton* 2>/dev/null || true
nohup "$CHROME" --user-data-dir="$PROFILE" --remote-debugging-port="$PORT" \
  --no-first-run --no-default-browser-check --password-store=basic about:blank \
  >"$BROWSERS/$SLOT.log" 2>&1 &
echo $! > "$PIDFILE"; echo "$PORT" > "$BROWSERS/$SLOT.port"
echo "$SLOT started on port $PORT (pid $!). Connect with http://127.0.0.1:$PORT"
```
