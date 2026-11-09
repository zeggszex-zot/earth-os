#!/usr/bin/env bash
# Run the conformance harness on this machine and keep the report.
#
# The experiment is not something this script can finish on its own. Run it on
# each machine you want to compare, collect the JSON files somewhere, and then
# compare them in pairs:
#
#     # on the Debian box
#     ./tools/multi-distro.sh debian12
#     # on the Kali box
#     ./tools/multi-distro.sh kali
#     # anywhere, with both files
#     python3 tools/conformance.py --compare reports/debian12.json reports/kali.json
#
# What you are looking for is a report that says several mechanisms differed
# and no capability disagreed. A run where *nothing* differed is not a pass:
# it means the two machines had identical tooling, which is one computer
# measured twice.
#
# Nothing here modifies the machine. Effectful capabilities are listed and
# never invoked — a conformance run must not reboot the computer it is
# measuring.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="${1:-$(. /etc/os-release 2>/dev/null && echo "${ID:-unknown}${VERSION_ID:-}" || echo unknown)}"
OUT="${CDOS_REPORTS:-$HERE/reports}"
mkdir -p "$OUT"

# The suite needs Python 3.12 or newer where PyGObject's extension is built
# for it; the conformance harness itself does not touch GTK and runs on any
# Python 3.9+.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
fi
[ -n "$PY" ] || { echo "no python3 found" >&2; exit 1; }

echo "CDOS conformance — $NAME — $($PY -V 2>&1)"
echo

"$PY" "$HERE/tools/conformance.py" --json "$OUT/$NAME.json"
status=$?

echo
echo "Report: $OUT/$NAME.json"
echo "Compare with another machine:"
echo "  $PY tools/conformance.py --compare $OUT/$NAME.json $OUT/OTHER.json"
exit $status
