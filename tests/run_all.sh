#!/usr/bin/env bash
# checker's full adversarial suite. Run from anywhere:
#     bash tests/run_all.sh
#
# Exit code 0 only if every file reports no finding. Each file is independent
# and prints its own evidence; nothing here is a silent pass.
#
# test_weights_eval.py needs numpy, so it uses the ml env when available.

set -u
cd "$(dirname "$0")/.."
ROOT="$PWD"
PY=python3
ML="${TETRIS_PY:-$PY}"   # torch 가 있는 파이썬. 없으면 그 테스트만 skip 된다
[ -x "$ML" ] || ML="$PY"

declare -a NAMES=(
  "test_score_golden      engine  GOLDEN absolute score vs spec section 5"
  "test_difficulty        engine  difficulty modes: bypass, invariance, collapse"
  "test_search_rule       rl      1-ply search rule: does the fix help?"
  "test_round9_parity     web/rl  tie-break rule agreement (Python vs JS)"
  "test_policy_layer_parity web/rl POLICY layer: does JS choose the same move?"
  "test_wells10_features  rl      third implementation of the 2 new features"
  "test_median_recompute  rl      independent recompute of the 200-seed medians"
  "test_cem_variance_collapse rl  CEM sigma collapse: is the compute doing anything?"
  "test_is_stuck_guard    engine  _is_stuck cost guard: differential + formal"
  "test_parity_coverage   engine  Python<->JS parity coverage + fixtures"
  "test_rules_spec        engine  spec.md rules + HUMAN-path parity"
  "test_info_contract     engine  info dict contract vs its consumers"
  "test_web_deploy        web     deployment, weights reachability, bundle"
  "test_weights_eval      rl      fresh-seed re-eval + fastsim<->engine"
  "test_ui_playwright     web     REAL BROWSER: keys, AI restart, 8 panels"
)

fails=()
for row in "${NAMES[@]}"; do
  f=$(echo "$row" | awk '{print $1}')
  owner=$(echo "$row" | awk '{print $2}')
  desc=$(echo "$row" | cut -d' ' -f3- | sed 's/^ *[a-z]* *//')
  echo
  echo "================================================================"
  echo "  $f   (code under test: $owner)"
  echo "================================================================"
  case "$f" in
    test_weights_eval|test_ui_playwright|test_median_recompute|test_wells10_features) RUN="$ML" ;;
    *) RUN="$PY" ;;
  esac
  if "$RUN" "$ROOT/tests/$f.py"; then
    echo "VERDICT $f: CLEAN"
  else
    echo "VERDICT $f: FINDINGS (exit $?)"
    fails+=("$f")
  fi
done

echo
echo "================================================================"
# The verdict is printed on ONE grep-able line with a fixed prefix, because a
# filtered view of this output must never be able to hide it. (checker grepped
# for "FINDINGS IN" instead of "FINDINGS OPEN IN" once and briefly concluded
# the runner was swallowing failures -- the runner was fine, the filter was not.
# Piping this script into grep also replaces its exit code with grep's, so read
# the SUITE_VERDICT line, not $?.)
echo "SUITE_TOTAL: ${#NAMES[@]} files"
if [ ${#fails[@]} -eq 0 ]; then
  echo "SUITE_VERDICT: CLEAN (0 of ${#NAMES[@]} files reported findings)"
  exit 0
fi
echo "SUITE_VERDICT: FINDINGS in ${#fails[@]} of ${#NAMES[@]} files: ${fails[*]}"
echo "Scroll up for the (1) claim (2) counterexample (3) expected vs actual"
echo "(4) why-it-leaks breakdown of each."
exit 1
