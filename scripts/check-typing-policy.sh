#!/usr/bin/env bash
# Typing-suppression policy for the python packages (runs first in `task typecheck`;
# pyright strict enforces everything type-shaped, this enforces what pyright can't
# express — WHICH escapes are allowed):
#
#   * `# type: ignore` in any form is banned (it's also inert: pyrightconfig sets
#     enableTypeIgnoreComments=false, so it would be a dead comment anyway).
#   * `# pyright:` file directives are banned except rule-scoped ignores — no bare
#     `# pyright: ignore`, no per-file mode downgrades (`# pyright: basic`, or
#     rule=false toggles). A file cannot opt out of strict.
#   * rule-scoped ignores are ALLOWLISTED below: each rule earned its place by one
#     of the two sanctioned reasons — runtime validation stronger than the static
#     contract, or a named third-party typing gap. Adding a rule here is a policy
#     change; make it in this file, in a reviewed diff, with the reason.
#   * `cast()` is allowlisted BY FILE: it is only sanctioned at declared dynamic
#     third-party boundaries. A new cast anywhere else fails here — either give the
#     value a real type, or argue the file into the list.
set -euo pipefail
cd "$(dirname "$0")/.."

PY_ROOTS=(runner/src lib/reps-testing/reps_testing lib/reps-events/reps_events lib/reps-providers/reps_providers
          lib/reps-experiment/reps_experiment lib/reps-inspect/reps_inspect)

# Rules that MAY appear in `# pyright: ignore[...]`, and why:
#   reportUnnecessaryIsInstance   emit-side runtime guards: callers are untyped
#                                 experiment code, annotations enforce nothing there
#   reportUnknownVariableType     third-party symbols whose own signatures carry
#                                 untyped parts (inspect_ai's eval / Scanner)
#   reportUntypedClassDecorator   third-party decorators shipped untyped
#                                 (inspect_ai's @hooks)
ALLOWED_IGNORE_RULES="reportUnnecessaryIsInstance reportUnknownVariableType reportUntypedClassDecorator"

# Files that MAY contain cast(); currently only the OpenAI-SDK response ingress,
# whose module contract is deliberately dynamic (duck-typed client).
ALLOWED_CAST_FILES="lib/reps-experiment/reps_experiment/llm.py"

fail=0
say() { echo "typing-policy: $*" >&2; fail=1; }

# 1. no `# type: ignore` of any shape
while IFS= read -r hit; do
  say "banned '# type: ignore' (use a rule-scoped '# pyright: ignore[rule]'): $hit"
done < <(grep -rn '# *type: *ignore' "${PY_ROOTS[@]}" --include='*.py' || true)

# 2. `# pyright:` comments must be exactly rule-scoped ignores
while IFS= read -r hit; do
  say "banned pyright directive (only '# pyright: ignore[rule]' is allowed): $hit"
done < <(grep -rn '# *pyright:' "${PY_ROOTS[@]}" --include='*.py' \
         | grep -v '# pyright: ignore\[' || true)

# 3. ignored rules must be on the allowlist
while IFS= read -r hit; do
  file_line=${hit%%'	'*}
  rules=$(sed 's/.*pyright: ignore\[\([^]]*\)\].*/\1/' <<<"$hit" | tr ',' ' ')
  for rule in $rules; do
    rule=$(tr -d ' ' <<<"$rule")
    case " $ALLOWED_IGNORE_RULES " in
      *" $rule "*) ;;
      *) say "ignore rule '$rule' is not allowlisted (see this script): ${hit%%:*}" ;;
    esac
  done
done < <(grep -rn 'pyright: ignore\[' "${PY_ROOTS[@]}" --include='*.py' || true)

# 4. cast() only in allowlisted files
while IFS= read -r hit; do
  f=${hit%%:*}
  case " $ALLOWED_CAST_FILES " in
    *" $f "*) ;;
    *) say "cast() outside the allowlist (type the value instead, or argue the file in): $hit" ;;
  esac
done < <(grep -rn '\bcast(' "${PY_ROOTS[@]}" --include='*.py' | grep -v 'import' || true)

if [ "$fail" -ne 0 ]; then
  echo "typing-policy: FAILED — see docs above each check in scripts/check-typing-policy.sh" >&2
  exit 1
fi
echo "typing-policy: ok"
