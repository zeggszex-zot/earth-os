#!/usr/bin/env bash
# Install CDOS as system services on a Linux machine with systemd (a NUC, a laptop, a Pi): code, configuration and
# state kept apart, so the code can be replaced without touching what CDOS knows.
#
#   sudo tools/install_services.sh                  install or upgrade, enable cdos-api and the growth timer
#   sudo tools/install_services.sh --robot-sim      also the simulated robot
#   tools/install_services.sh --prefix P --config-dir C --state-dir S --unit-dir U --no-systemctl --no-user
#                                                   the same into any folders (packaging, testing), touching nothing else
#
#   PREFIX     /opt/cdos-computer      releases/<id>/ (the code, replaceable) and current -> the active release
#   CONFIG     /etc/cdos-computer      config.toml (written once, never overwritten), plugins/ (admin-only: it is code)
#   CONFIG                             governor.json: who may do what (root-owned; written once; edit it to name operators)
#   STATE      /var/lib/cdos           data/ (corpus), models/neural/ (registry, growth, experience), log/, state/, robotics/,
#                                      governor/ (the audit log and the emergency-stop latch)
#
# An upgrade installs a new release and moves `current`; CONFIG and STATE are left as they are.  The old release stays
# for rollback (ln -sfn releases/<old id> PREFIX/current, then restart the services).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/cdos-computer
CONFIG=/etc/cdos-computer
STATE=/var/lib/cdos
UNITS=/etc/systemd/system
SVC_USER=cdos
SYSTEMCTL=1
MKUSER=1
ROBOT=0
PYTHON=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift ;;
    --config-dir) CONFIG="$2"; shift ;;
    --state-dir) STATE="$2"; shift ;;
    --unit-dir) UNITS="$2"; shift ;;
    --user) SVC_USER="$2"; shift ;;
    --python) PYTHON="$2"; shift ;;
    --no-systemctl) SYSTEMCTL=0 ;;
    --no-user) MKUSER=0 ;;
    --robot-sim) ROBOT=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
for d in "$PREFIX" "$CONFIG" "$STATE" "$UNITS"; do
  case "$d" in /*) ;; *) echo "paths must be absolute: $d" >&2; exit 2 ;; esac
done
say() { printf '[cdos] %s\n' "$*"; }

if [ "$MKUSER" = 1 ] && ! id "$SVC_USER" >/dev/null 2>&1; then
  say "service user $SVC_USER"
  useradd --system --home-dir "$STATE" --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi

# 1. the code: a new release, then `current` moves to it in one rename
REL_ID="$(cd "$SRC" && find computer tools plugins systemd -type f -name '*' ! -path '*/__pycache__/*' -print0 \
          | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12)"
REL="$PREFIX/releases/$REL_ID"
if [ ! -d "$REL" ]; then
  say "release $REL_ID -> $REL"
  mkdir -p "$PREFIX/releases"
  TMP="$PREFIX/releases/.tmp-$$"
  rm -rf "$TMP"; mkdir -p "$TMP"
  (cd "$SRC" && tar --exclude='__pycache__' -cf - computer tools plugins systemd docs README.md config.example.toml) \
    | (cd "$TMP" && tar -xf -)
  mv "$TMP" "$REL"
fi
ln -sfn "releases/$REL_ID" "$PREFIX/.current-$$" && mv -Tf "$PREFIX/.current-$$" "$PREFIX/current"
if [ -z "$PYTHON" ]; then
  if [ ! -x "$PREFIX/venv/bin/python3" ]; then
    say "python environment $PREFIX/venv (system packages visible: numpy, if installed, is used for training)"
    python3 -m venv --system-site-packages "$PREFIX/venv"
  fi
  PYTHON="$PREFIX/venv/bin/python3"
fi

# 2. state: created if missing, never emptied
for d in data models/neural log state robotics governor; do mkdir -p "$STATE/$d"; done

# 3. configuration: written once
mkdir -p "$CONFIG/plugins"
if [ ! -f "$CONFIG/config.toml" ]; then
  say "configuration $CONFIG/config.toml"
  cat > "$CONFIG/config.toml" <<EOF
# CDOS service configuration (tools/install_services.sh writes this once; edit it freely: upgrades keep it).
corpus = "$STATE/data"
corpus_integrity = "verify"
llm_mode = "off"
llm_endpoint = ""
log_file = "$STATE/log/computer.log.jsonl"
state_file = "$STATE/state/computer.state.json"
neural_registry = "$STATE/models/neural"
neural_experience = true
growth_approval = "manual"
growth_auto_run = true
plugins_dir = "$CONFIG/plugins"
robotics = $([ "$ROBOT" = 1 ] && echo true || echo false)
robotics_journal = "$STATE/robotics/decisions.jsonl"
api_socket = "/run/cdos/api.sock"
governor_policy = "$CONFIG/governor.json"
governor_dir = "$STATE/governor"
EOF
elif ! grep -q '^governor_policy' "$CONFIG/config.toml"; then
  say "NOTE: $CONFIG/config.toml (kept from before) has no Governor settings. Until you add them, nobody can clear"
  say "      the emergency stop or promote a model. Add:"
  say "        governor_policy = \"$CONFIG/governor.json\""
  say "        governor_dir = \"$STATE/governor\""
fi
if [ ! -f "$CONFIG/governor.json" ]; then
  say "governor policy $CONFIG/governor.json (operators: root; robot and service: $SVC_USER)"
  cat > "$CONFIG/governor.json" <<EOF
{
 "format": "cdos-governor-policy/1",
 "comment": "Who may do what (docs/GOVERNOR.md). Login names or uid:N. Keep this file owned by root. The service user must not be an operator.",
 "roles": {"operator": ["root"], "robot": ["$SVC_USER"], "service": ["$SVC_USER"], "teacher": []},
 "default": ["read", "ask"],
 "unverified": ["read", "ask"]
}
EOF
fi
if [ -z "$(ls -A "$CONFIG/plugins" 2>/dev/null)" ]; then
  cp -r "$REL/plugins/." "$CONFIG/plugins/"        # the example plugin; remove it if you don't want it
fi

# 4. units
mkdir -p "$UNITS"
for u in cdos-api.service cdos-growth.service cdos-growth.timer cdos-robot-sim.service; do
  sed -e "s#@PREFIX@#$PREFIX#g" -e "s#@CONFIG@#$CONFIG#g" -e "s#@STATE@#$STATE#g" -e "s#@USER@#$SVC_USER#g" \
      -e "s#@PYTHON@#$PYTHON#g" "$REL/systemd/$u" > "$UNITS/$u"
done

if [ "$MKUSER" = 1 ]; then
  chown -R "$SVC_USER:$SVC_USER" "$STATE"
  chown -R root:root "$CONFIG" && chmod 755 "$CONFIG" && chmod 644 "$CONFIG/config.toml" "$CONFIG/governor.json"
  chmod 750 "$STATE/governor"
fi
if [ "$SYSTEMCTL" = 1 ]; then
  systemctl daemon-reload
  systemctl enable --now cdos-api.service cdos-growth.timer
  [ "$ROBOT" = 1 ] && systemctl enable --now cdos-robot-sim.service
  systemctl restart cdos-api.service
fi
[ -f "$STATE/data/meta.json" ] || say "no corpus yet: copy your data/ folder into $STATE/data (CDOS starts without it)"
say "installed release $REL_ID; config $CONFIG/config.toml; state $STATE"
if [ "$ROBOT" = 1 ]; then
  say "the emergency stop starts ENGAGED. When the robot is ready, from $PREFIX/current:"
  say "  sudo $PYTHON -m computer.governor --config $CONFIG/config.toml estop clear --reason \"...\""
fi
