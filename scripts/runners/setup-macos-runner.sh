#!/usr/bin/env bash
# One-command setup of a self-hosted GitHub Actions runner for ApplyQueue on macOS.
#
# Intended one-liner (see docs/applyqueue/CI_RUNNERS.md) - paste into Terminal as your normal
# (admin) user, NOT with sudo:
#
#   command -v gh >/dev/null || { command -v brew >/dev/null || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; eval "$(/usr/local/bin/brew shellenv 2>/dev/null || /opt/homebrew/bin/brew shellenv)"; brew install gh; } && { gh auth status >/dev/null 2>&1 || gh auth login -h github.com -p https -w; } && gh api -H 'Accept: application/vnd.github.raw' 'repos/JonathanLiu1401/ApplyQueue/contents/scripts/runners/setup-macos-runner.sh?ref=main' > /tmp/aq-runner-setup.sh && bash /tmp/aq-runner-setup.sh
#
# The repo is private, so the script is fetched with the signed-in gh (no raw URL, no token in
# the command). gh itself comes from Homebrew, which is installed first if missing.
#
# What it does (idempotent, safe to rerun):
#   1. Refuses Apple silicon unless --allow-arm64 (Intel/x86_64 is the target; Rosetta is detected).
#   2. Xcode Command Line Tools (opens Apple's dialog if missing and waits for it to finish).
#   3. Homebrew, then gh, git, Node LTS, Python 3.11 and uv. Rust via rustup (+ x86_64-apple-darwin).
#   4. gh sign-in if needed (browser), checks you are an admin of the repo.
#   5. Downloads the latest official actions/runner (osx-x64 or osx-arm64), verifies its SHA-256,
#      registers it to JonathanLiu1401/ApplyQueue ONLY, installs + starts it as a launchd service.
#   6. Keeps the Mac awake while on the charger (pmset -c sleep 0), unless --no-keep-awake.
#
# The registration token is fetched with gh (or read from --token-stdin / RUNNER_TOKEN), passed to
# config.sh through the environment only, and never written to disk, the shell history or the log.
#
# Options:
#   --allow-arm64        allow Apple silicon (labels: self-hosted,macOS,ARM64,apple-silicon,applyqueue)
#   --name NAME          runner name (default: applyqueue-mac-intel-<hostname> / applyqueue-mac-arm64-<hostname>)
#   --token-stdin        read a registration token from stdin instead of fetching one with gh
#   --no-keep-awake      do not change pmset sleep settings
#   --skip-prereqs       only (re)install the runner
#   --uninstall          stop + remove the service and unregister the runner
set -euo pipefail
set +x

REPO="JonathanLiu1401/ApplyQueue"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-applyqueue}"
ALLOW_ARM64=0; NAME=""; TOKEN_STDIN=0; KEEP_AWAKE=1; SKIP_PREREQS=0; UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --allow-arm64) ALLOW_ARM64=1 ;;
    --name) NAME="$2"; shift ;;
    --token-stdin) TOKEN_STDIN=1 ;;
    --no-keep-awake) KEEP_AWAKE=0 ;;
    --skip-prereqs) SKIP_PREREQS=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this script is for macOS"
[ "$(id -u)" != 0 ] || die "run as your normal user, not with sudo (the runner must not run as root)"

# --- architecture: hw.optional.arm64 is 1 on Apple silicon even inside a Rosetta shell ----------
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
  ARCH=arm64
  [ "$ALLOW_ARM64" = 1 ] || die "this is an Apple silicon Mac. The Intel runner needs an Intel Mac. Rerun with --allow-arm64 to register an Apple silicon runner instead."
  [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ] && die "this Terminal runs under Rosetta; open a native (arm64) Terminal"
  RUNNER_PKG=osx-arm64; LABELS="self-hosted,macOS,ARM64,apple-silicon,applyqueue"; RUST_TARGET=aarch64-apple-darwin
else
  ARCH=x64
  RUNNER_PKG=osx-x64; LABELS="self-hosted,macOS,X64,intel,applyqueue"; RUST_TARGET=x86_64-apple-darwin
fi
HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
[ -n "$NAME" ] || { [ "$ARCH" = x64 ] && NAME="applyqueue-mac-intel-$HOST" || NAME="applyqueue-mac-arm64-$HOST"; }
echo "Mac: $ARCH ($(sw_vers -productVersion)), runner name: $NAME, labels: $LABELS"

brew_env() {
  for b in /usr/local/bin/brew /opt/homebrew/bin/brew; do
    [ -x "$b" ] && { eval "$("$b" shellenv)"; return 0; }
  done
  return 1
}
brew_env || true
# shellcheck disable=SC1091
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"

runner_id() {  # id of a registered runner with our name, empty if none
  gh api "repos/$REPO/actions/runners?per_page=100" --jq ".runners[] | select(.name==\"$NAME\") | .id" 2>/dev/null || true
}

# --- uninstall -----------------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
  say "Removing runner $NAME"
  if [ -x "$RUNNER_DIR/svc.sh" ]; then (cd "$RUNNER_DIR" && ./svc.sh stop || true; ./svc.sh uninstall || true); fi
  if [ -f "$RUNNER_DIR/.runner" ]; then
    RT="$(gh api -X POST "repos/$REPO/actions/runners/remove-token" --jq .token)"
    (cd "$RUNNER_DIR" && ACTIONS_RUNNER_INPUT_TOKEN="$RT" ./config.sh remove) || true
    unset RT
  fi
  id="$(runner_id)"; [ -n "$id" ] && gh api -X DELETE "repos/$REPO/actions/runners/$id" && echo "deleted runner $id on GitHub"
  echo "Runner removed. Delete $RUNNER_DIR to free disk space."
  exit 0
fi

# --- prerequisites --------------------------------------------------------------------------------
if [ "$SKIP_PREREQS" = 0 ]; then
  say "Xcode Command Line Tools"
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install >/dev/null 2>&1 || true
    echo "A dialog asked to install the Command Line Tools: click Install, accept the licence, and wait."
    echo "Waiting for it to finish (up to 60 minutes)..."
    for _ in $(seq 1 360); do xcode-select -p >/dev/null 2>&1 && break; sleep 10; done
    xcode-select -p >/dev/null 2>&1 || die "Command Line Tools still missing. Finish the dialog, then rerun this command."
  fi
  echo "ok: $(xcode-select -p)"

  say "Homebrew"
  if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew (it asks for your Mac password)."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew_env || die "Homebrew install failed"
  fi
  echo "ok: $(brew --prefix)"

  say "gh, git, Node LTS, Python 3.11, uv"
  brew list --formula gh >/dev/null 2>&1 || brew install gh
  for f in git node@24 python@3.11 uv; do brew list --formula "$f" >/dev/null 2>&1 || brew install "$f"; done
  brew link --overwrite node@24 >/dev/null 2>&1 || true
  echo "ok: node $(node --version 2>/dev/null || echo '(keg)'), $(python3.11 --version), uv $(uv --version | awk '{print $2}')"

  say "Rust (rustup)"
  if ! command -v rustup >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
    # shellcheck disable=SC1091
    . "$HOME/.cargo/env"
  else
    rustup update stable --no-self-update >/dev/null
  fi
  rustup target add "$RUST_TARGET" >/dev/null
  echo "ok: $(rustc --version) target $RUST_TARGET"
fi

command -v gh >/dev/null 2>&1 || die "gh is not installed (rerun without --skip-prereqs)"

# --- GitHub sign-in + permission check ------------------------------------------------------------
say "GitHub sign-in"
if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "A browser window opens; sign in to GitHub as the owner of $REPO and enter the code shown here."
  gh auth login -h github.com -p https -w
fi
[ "$(gh api "repos/$REPO" --jq .permissions.admin 2>/dev/null)" = "true" ] \
  || die "the signed-in GitHub account is not an admin of $REPO (run: gh auth login)"
echo "ok: signed in as $(gh api user --jq .login)"

# --- runner download (latest official release, SHA-256 verified) ----------------------------------
say "GitHub Actions runner"
mkdir -p "$RUNNER_DIR"; cd "$RUNNER_DIR"
VER="$(gh api repos/actions/runner/releases/latest --jq .tag_name)"; VER="${VER#v}"
HAVE="$( [ -x bin/Runner.Listener ] && ./bin/Runner.Listener --version 2>/dev/null || echo none)"
if [ "$HAVE" = "none" ]; then
  TGZ="actions-runner-$RUNNER_PKG-$VER.tar.gz"
  SHA="$(gh api repos/actions/runner/releases/latest --jq .body | sed -n "s/.*BEGIN SHA $RUNNER_PKG -->\([0-9a-f]\{64\}\)<.*/\1/p" | head -1)"
  [ -n "$SHA" ] || die "could not read the runner checksum from the release notes"
  curl -fsSL -o "$TGZ" "https://github.com/actions/runner/releases/download/v$VER/$TGZ"
  echo "$SHA  $TGZ" | shasum -a 256 -c - || { rm -f "$TGZ"; die "runner checksum mismatch"; }
  tar xzf "$TGZ" && rm -f "$TGZ"
  echo "installed runner $VER"
else
  echo "runner $HAVE already installed (it self-updates)"
fi

# --- registration (repo-scoped) -------------------------------------------------------------------
REGISTERED_ID="$(runner_id)"
if [ -f .runner ] && [ -n "$REGISTERED_ID" ]; then
  echo "already registered as $NAME (id $REGISTERED_ID)"
else
  if [ -f .runner ]; then  # local config left over from a runner deleted on GitHub
    [ -x svc.sh ] && { ./svc.sh stop >/dev/null 2>&1 || true; ./svc.sh uninstall >/dev/null 2>&1 || true; }
    rm -f .runner .credentials .credentials_rsaparams .service
  fi
  if [ "$TOKEN_STDIN" = 1 ]; then IFS= read -r REG_TOKEN
  elif [ -n "${RUNNER_TOKEN:-}" ]; then REG_TOKEN="$RUNNER_TOKEN"; unset RUNNER_TOKEN
  else REG_TOKEN="$(gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token)"; fi
  [ -n "$REG_TOKEN" ] || die "no registration token"
  ACTIONS_RUNNER_INPUT_TOKEN="$REG_TOKEN" ./config.sh --unattended \
    --url "https://github.com/$REPO" --name "$NAME" --labels "$LABELS" --no-default-labels \
    --work _work --replace
  unset REG_TOKEN
fi

# Refresh PATH/env the service sees (Homebrew, cargo) so jobs find the toolchain.
./env.sh >/dev/null

# --- launchd service --------------------------------------------------------------------------------
say "launchd service"
if ls "$HOME/Library/LaunchAgents"/actions.runner.*"$NAME"*.plist >/dev/null 2>&1; then
  ./svc.sh stop >/dev/null 2>&1 || true
else
  ./svc.sh install
fi
./svc.sh start
./svc.sh status | tail -3 || true

if [ "$KEEP_AWAKE" = 1 ]; then
  say "Keep awake on the charger"
  if [ "$(pmset -g custom 2>/dev/null | awk '/AC Power/{f=1} f&&$1=="sleep"{print $2; exit}')" != "0" ]; then
    echo "Setting 'never sleep while plugged in' (asks for your Mac password)."
    sudo pmset -c sleep 0 disksleep 0 || echo "skipped (set it in System Settings > Battery/Energy instead)"
  else
    echo "ok: already never sleeps on AC power"
  fi
fi

say "Checking the runner is online"
for _ in $(seq 1 12); do
  st="$(gh api "repos/$REPO/actions/runners?per_page=100" --jq ".runners[] | select(.name==\"$NAME\") | .status" 2>/dev/null || true)"
  [ "$st" = "online" ] && break; sleep 5
done
echo "runner $NAME: ${st:-unknown}"
[ "${st:-}" = "online" ] || die "runner not online yet; check $RUNNER_DIR/_diag and './svc.sh status'"
printf '\n\033[32mDone. %s is online for %s with labels %s.\033[0m\n' "$NAME" "$REPO" "$LABELS"
echo "Keep this Mac logged in, plugged in and connected. Remove later with: bash $0 --uninstall"
