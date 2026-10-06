#!/usr/bin/env bash
# Every entrypoint — nix run (flakes), nix run -f (flakes CLI over classic attrs),
# $(nix-build …) (classic), and nix-run — must mint IDENTICAL condition identity
# (source content hash + condition id) for the same experiment and params on the
# same tree. This guards against eval-path drift (flake store copies, path
# filtering, arg plumbing) silently fragmenting conditions by entrypoint.
# Keyless: --dry-run only (nothing executes, nothing is written to the databank).
#
#   check-entrypoint-identity.sh [--dir DIR] [--exp NAME] [-- --set k=v …]
#
# Default: inspect-hello on mockllm in the reps checkout — four doors. The flake
# door sees git-TRACKED files only, so a mismatch there can also mean a file that
# should be tracked isn't yet. --dir can select another REPS checkout.
set -euo pipefail

dir="$(cd "$(dirname "$0")/.." && pwd)"
exp=inspect-hello
params=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) dir="$(cd "$2" && pwd)"; shift 2 ;;
    --exp) exp="$2"; shift 2 ;;
    --) shift; params=("$@"); break ;;
    *) echo "usage: $0 [--dir DIR] [--exp NAME] [-- --set k=v …]" >&2; exit 2 ;;
  esac
done
if [ "${#params[@]}" -eq 0 ]; then
  params=(--set model=mockllm/model --set limit=0 --set epochs=1 --set 'generate_args={}')
fi
args=("${params[@]}" --dry-run)
cd "$dir"

# dry-run output carries:  "source:     content:sha256:…"  and
# "condition <abbrev>  (<full-cid>)" — the base seed line is random and ignored
extract() { awk '/^source:/ { print $2 } /^condition / { gsub(/[()]/, "", $NF); print $NF }'; }

names=() idents=()

echo "entrypoint: nix-build" >&2
names+=(nix-build)
idents+=("$("$(nix-build --no-out-link -A "exec.$exp")" "${args[@]}" | extract)")

echo "entrypoint: nix run -f" >&2
names+=("nix run -f")
idents+=("$(nix run -f . "experiment-$exp" --extra-experimental-features 'nix-command flakes' -- "${args[@]}" | extract)")

if [ -e flake.nix ]; then
  echo "entrypoint: flakes" >&2
  names+=(flakes)
  idents+=("$(nix run ".#$exp" --extra-experimental-features 'nix-command flakes' -- "${args[@]}" | extract)")
fi

echo "entrypoint: nix-run" >&2
names+=(nix-run)
idents+=("$(nix-shell -p nix-run --run "nix-run . -A experiment-$exp -- $(printf '%q ' "${args[@]}")" | extract)")

ok=0
for i in "${!names[@]}"; do
  [ "${idents[$i]}" = "${idents[0]}" ] || ok=1
done
if [ "$ok" -eq 0 ]; then
  printf 'OK: all entrypoints agree\n%s\n' "${idents[0]}"
else
  printf 'MISMATCH across entrypoints\n' >&2
  for i in "${!names[@]}"; do
    printf -- '-- %s --\n%s\n' "${names[$i]}" "${idents[$i]}" >&2
  done
  exit 1
fi
