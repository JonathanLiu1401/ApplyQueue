#!/usr/bin/env bash
# Supervise the ApplyQueue self-hosted Linux runner on a host WITHOUT systemd
# (the shared Grok Bot box runs tini as PID 1, so svc.sh cannot be used).
#
#   sudo bash scripts/runners/linux-box-runner.sh start    # start (idempotent)
#   sudo bash scripts/runners/linux-box-runner.sh stop
#   sudo bash scripts/runners/linux-box-runner.sh status
#
# The runner always runs as the unprivileged user RUNNER_USER (no sudo, no access to the
# admin's home). The loop restarts run.sh if it exits (crash, self-update) and prunes old
# _diag logs so the disk does not fill. After a host reboot run "start" once again.
# On a host WITH systemd use the runner's own svc.sh instead (see docs/applyqueue/CI_RUNNERS.md).
set -euo pipefail

RUNNER_USER="${RUNNER_USER:-aqrunner}"
RUNNER_HOME="$(getent passwd "$RUNNER_USER" | cut -d: -f6)"
RUNNER_DIR="${RUNNER_DIR:-$RUNNER_HOME/actions-runner-applyqueue}"
PIDFILE="$RUNNER_HOME/runner-supervisor.pid"
LOG="$RUNNER_HOME/runner-supervisor.log"

as_runner() { if [ "$(id -un)" = "$RUNNER_USER" ]; then bash -c "$1"; else sudo -u "$RUNNER_USER" -H bash -c "$1"; fi; }

running() { as_runner "[ -f '$PIDFILE' ] && kill -0 \$(cat '$PIDFILE') 2>/dev/null"; }

case "${1:-status}" in
  start)
    [ "$(id -u)" = 0 ] || [ "$(id -un)" = "$RUNNER_USER" ] || exec sudo -- "$0" "$@"
    [ -x "$RUNNER_DIR/run.sh" ] || { echo "runner not installed in $RUNNER_DIR" >&2; exit 1; }
    if running; then echo "already running (pid $(as_runner "cat '$PIDFILE'"))"; exit 0; fi
    as_runner "cd '$RUNNER_DIR' && setsid nohup bash -c '
      echo \$\$ > \"$PIDFILE\"
      trap \"pkill -TERM -P \$\$; exit 0\" TERM INT
      while true; do
        find \"$RUNNER_DIR/_diag\" -type f -mtime +7 -delete 2>/dev/null || true
        [ \$(stat -c %s \"$LOG\" 2>/dev/null || echo 0) -gt 1000000 ] && : > \"$LOG\"
        echo \"[\$(date -Is)] starting run.sh\"
        ./run.sh & wait \$!
        echo \"[\$(date -Is)] run.sh exited with \$?; restarting in 15s\"
        sleep 15
      done' >> '$LOG' 2>&1 < /dev/null &"
    sleep 2; running && echo "started (pid $(as_runner "cat '$PIDFILE'")), log: $LOG"
    ;;
  stop)
    [ "$(id -u)" = 0 ] || [ "$(id -un)" = "$RUNNER_USER" ] || exec sudo -- "$0" "$@"
    if running; then
      pid=$(as_runner "cat '$PIDFILE'")
      as_runner "kill -TERM -- -$pid 2>/dev/null || kill -TERM $pid"; as_runner "rm -f '$PIDFILE'"
      echo "stopped"
    else echo "not running"; fi
    ;;
  status)
    if running; then echo "running (pid $(as_runner "cat '$PIDFILE'"))"; else echo "not running"; exit 3; fi
    ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
