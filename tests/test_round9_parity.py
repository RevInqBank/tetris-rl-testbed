"""Python vs JS tie-breaking. TWO SEPARATE FACTS, do not conflate them.

  FACT 1 -- THE CAUSE OF web's ENDGAME DIVERGENCE WAS NOT ROUNDING.
      It was an undocumented death penalty (DEATH = -1e6) that
      `web/policies.js:scoreAll` applied to game-over placements and rl's
      greedy path did not. Removing it made all 10 seeds match exactly.
      It hid for 11,700 moves because a game-over placement only exists once
      the stack nearly touches the ceiling: on seed 900009 only 29 of 11,707
      moves had one, six of them in the last eight moves.

  FACT 2 -- THE ROUNDING MISMATCH WAS REAL BUT WAS NEVER THE CAUSE, AND IS
      NOW GONE. I measured that `Math.round(v*1e9)/1e9` (JS) and
      `round(v, 9)` (Python) disagree on negatives, and 302 of 708 exact
      half-way values did split. web then measured 42,594 REAL game scores
      and found ZERO mismatches -- the distribution simply does not produce
      those values. My "the disagreement rate matches the observed split
      point" argument was a COINCIDENCE: an unrelated mechanism of similar
      size. Both sides have since moved to an epsilon comparison on raw
      scores, adopted on structural merit, so the mechanism no longer exists.

  WHAT MY HYPOTHESIS GOT RIGHT: the DIRECTION. I argued the bias was
      one-directional (JS makes a placement look better) and that a symmetric
      float error cannot do that. web had independently observed JS surviving
      longer on all three seeds. The direction was correct; only the mechanism
      was wrong -- avoiding death is what made it survive longer.

  LESSON I OWE THIS FILE: when handing over a hypothesis, GRADE THE EVIDENCE.
      Directional evidence is strong; magnitude agreement is weak and dies to
      coincidence. I presented all three signals with equal weight, and two of
      them were the weak kind.

This file now verifies the CURRENT tie-break rule rather than the historical
one, and fails if the two sides ever diverge on it again.

Original purpose (kept for the record):
Python vs JS rounding, as a candidate cause for web's endgame divergence.

THE OPEN DEFECT (docs/status_web.md, read 2026-08-07 21:11):
    web's JS run and rl's Python run agree for 11,700 moves on seed 900009 and
    then diverge in the last 3. Two other seeds diverge at 3,750 and 2,400.
    All four checkpoint items (board_hash, n_legal, chosen rotation, chosen
    leftmost column) match right up to the split, and non-determinism,
    counters, buffer rows and compare-before-round were all ruled out.

WHY MY PARITY TEST DID NOT CATCH THIS -- stated plainly, because it is a real
gap in my coverage and not a detail:
    `tests/test_parity_coverage.py` compares the ENGINE (legal_placements,
    apply_placement, board_hash, state_hash) between Python and JS. It found
    146,554 move records identical. That test is structurally incapable of
    seeing this bug, because the engine is not where the disagreement is: both
    engines agree on what a placement DOES. The disagreement is in the POLICY
    layer -- which placement gets CHOSEN -- and nothing in my suite compared
    `web/policies.js` against `rl/`'s scoring move by move. A green parity run
    said nothing about it.

THE CANDIDATE MECHANISM. Both sides round scores to 9 decimals before a strict
`>` comparison, so that near-ties resolve identically. But:

    Python  rl/features.py:argmax_stable   round(float(v), 9)
            -> correctly-rounded decimal, ties to EVEN
    JS      web/policies.js:round9         Math.round(v * 1e9) / 1e9
            -> ties to +INFINITY, and the multiply adds its own error

Placement scores here are NEGATIVE. On negative values those two rules round in
OPPOSITE directions. rl documented that one flipped tie diverges a game
completely (1,996 lines vs 800 on the same weights and seed), so a rare
disagreement is enough.

This file measures the disagreement rate and checks whether its size and its
DIRECTION match what web observed. It does not claim to have proved the cause.

Run:  python3 tests/test_round9_parity.py
Owners: web (round9), rl (argmax_stable). This file is checker-owned.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

#: web's observation, transcribed from docs/status_web.md (read 21:11)
WEB_SPLITS = {900009: 11700, 900001: 3750, 900004: 2400}
#: web: "세 시드 모두 내 쪽이 조금 더 오래 산다" (+30, +1, +4 pieces)
WEB_JS_LIVES_LONGER = True
#: typical branching factor of legal_placements on a mid-game board
PLACEMENTS_PER_MOVE = 34

PASSED, FAILED, SKIPPED = [], [], []


def ok(m):
    PASSED.append(m)
    print("  ok   %s" % m)


def fail(t, m):
    FAILED.append((t, m))
    print("  FAIL [%s] %s" % (t, m))


def skip(t, m):
    SKIPPED.append((t, m))
    print("  SKIP [%s] %s" % (t, m))


JS = """
import { readFileSync, writeFileSync } from 'fs';
const S = 1e9;
/* verbatim from web/policies.js */
const round9 = v => Math.round(v * S) / S;
const vals = JSON.parse(readFileSync(process.argv[2], 'utf8'));
writeFileSync(process.argv[3], JSON.stringify(vals.map(round9)));
"""


def js_round9(vals, tmp):
    runner = os.path.join(tmp, "r9.mjs")
    with open(runner, "w") as f:
        f.write(JS)
    fin, fout = os.path.join(tmp, "in.json"), os.path.join(tmp, "out.json")
    json.dump(vals, open(fin, "w"))
    subprocess.run(["node", runner, fin, fout], check=True,
                   capture_output=True)
    return json.load(open(fout))


def main():
    print("=" * 74)
    print("checker: round9 disagreement as a candidate for the endgame split")
    print("=" * 74)
    if not shutil.which("node"):
        skip("R0", "node not installed -- the rounding comparison did NOT run")
        return report()

    # confirm the two implementations are still what this test assumes
    pol = open(os.path.join(_ROOT, "web", "policies.js"), encoding="utf-8").read()
    feat = os.path.join(_ROOT, "rl", "features.py")
    ftxt = open(feat, encoding="utf-8").read() if os.path.exists(feat) else ""
    js_halfup = "Math.round(v * SCORE_SCALE)" in pol or "Math.round(v*SCORE_SCALE)" in pol
    py_builtin = "round(float(scores[" in ftxt or "round(float(" in ftxt
    print("\n0. are the two implementations still the ones this test assumes?")
    print("      web/policies.js uses Math.round(v*1e9)/1e9 : %s" % js_halfup)
    print("      rl/features.py  uses round(float(v), 9)    : %s" % py_builtin)
    # The regime can CHANGE under this test -- web and rl replaced rounding
    # with an epsilon guard after I reported the mismatch. A test whose premise
    # is gone must verify the REPLACEMENT, not skip and imply agreement.
    import re as _re
    js_eps = _re.search(r"SCORE_EPS\s*=\s*([0-9.eE+-]+)", pol)
    py_eps = _re.search(r"SCORE_EPS\s*=\s*([0-9.eE+-]+)", ftxt)
    js_cmp = "> scores[best] + SCORE_EPS" in pol
    py_cmp = "> best_v + SCORE_EPS" in ftxt
    print("      web/policies.js compares raw + SCORE_EPS   : %s (%s)"
          % (js_cmp, js_eps.group(1) if js_eps else "n/a"))
    print("      rl/features.py  compares raw + SCORE_EPS   : %s (%s)"
          % (py_cmp, py_eps.group(1) if py_eps else "n/a"))

    if not js_halfup and js_cmp and py_cmp and js_eps and py_eps:
        print("\n1'. BOTH sides now compare RAW scores with an epsilon guard")
        a, b = js_eps.group(1), py_eps.group(1)
        if float(a) != float(b):
            fail("R1", "the two tolerances DIFFER: JS %s vs Python %s. The "
                       "argmax tolerance must be identical or the two sides "
                       "resolve near-ties differently -- the same class of bug "
                       "as the rounding mismatch, one layer along." % (a, b))
        else:
            eps = float(a)
            ok("both sides use SCORE_EPS = %s with the same comparison shape "
               "(`v > best + EPS`, first maximum wins). Neither side rounds "
               "any more, so the half-to-even vs half-up divergence this file "
               "was written to measure CANNOT occur -- the mechanism is gone, "
               "not merely unobserved" % a)
            gap = abs(-23.665216819989176 - (-23.665216819989173))
            if gap < eps:
                ok("rl's documented worst accumulation gap (%.3g, numpy BLAS "
                   "vs a scalar loop) is %.0fx inside the tolerance (%.0e), so "
                   "last-bit differences can no longer flip an argmax"
                   % (gap, eps / gap, eps))
            else:
                fail("R1", "rl's documented accumulation gap %.3g is NOT inside "
                           "the tolerance %.0e -- last-bit differences can "
                           "still flip the argmax" % (gap, eps))
            ok("WHAT THIS DOES NOT PROVE: `v > best + EPS` is not transitive, "
               "so when several scores sit within EPS the winner depends on "
               "ITERATION ORDER. Both sides iterate legal_placements in the "
               "spec's fixed (rot, x) order, which is what makes it safe -- but "
               "that ordering is now load-bearing for the TIE-BREAK, not just "
               "for the parity trace. It is asserted in test_rules_spec.py.")
        return report()

    if not js_halfup:
        skip("R0", "web/policies.js no longer uses Math.round(v*1e9)/1e9, and "
                   "this test could not identify the replacement rule either. "
                   "NOTHING about tie-breaking was verified.")
        return report()
    if not py_builtin:
        print("      (could not confirm the Python side; continuing)")

    tmp = tempfile.mkdtemp()
    try:
        # --- 1. exact half-way boundaries, which is where the rules differ
        print("\n1. exact half-way boundaries at the 1e-9 place")
        vals = []
        for k in (-60, -23, -1, 0, 1, 23):
            for j in range(1, 60):
                vals.append(float(k) - (j + 0.5) * 1e-9)
                vals.append(float(k) + (j + 0.5) * 1e-9)
        js = js_round9(vals, tmp)
        py = [round(v, 9) for v in vals]
        bad = [(v, p, j) for v, p, j in zip(vals, py, js) if p != j]
        neg = [x for x in bad if x[0] < 0]
        pos = [x for x in bad if x[0] > 0]
        print("      %d boundary values, %d disagree (%d negative, %d positive)"
              % (len(vals), len(bad), len(neg), len(pos)))
        for v, p, j in bad[:3]:
            print("        v=%.17g  python=%.17g  js=%.17g  (js is %s)"
                  % (v, p, j, "higher" if j > p else "lower"))
        if not bad:
            ok("the two rounding rules agree even on exact half-way values -- "
               "this mechanism is not available and the endgame split needs "
               "another explanation")
            return report()
        fail("R1", "web/policies.js round9 and rl/features.py's round() "
                   "DISAGREE on %d of %d exact half-way values. Python's "
                   "round() is ties-to-even; Math.round is ties-to-+infinity, "
                   "and the *1e9 multiply adds error decimal rounding does not. "
                   "Placement scores are negative, and on negatives the two "
                   "rules go in OPPOSITE directions."
             % (len(bad), len(vals)))

        # --- 2. rate on realistic score values (this is what matters)
        print("\n2. rate on realistic negative score magnitudes")
        random.seed(11)
        N = 200000
        vals = [-random.uniform(0, 60) for _ in range(N)]
        js = js_round9(vals, tmp)
        py = [round(v, 9) for v in vals]
        bad = [(v, p, j) for v, p, j in zip(vals, py, js) if p != j]
        rate = len(bad) / float(N)
        print("      %d random values in [-60, 0): %d disagree (%.5f%%, "
              "1 in %s)" % (N, len(bad), 100 * rate,
                            "%.0f" % (1 / rate) if rate else "inf"))
        for v, p, j in bad[:3]:
            print("        v=%.17g  python=%.17g  js=%.17g" % (v, p, j))

        # --- 3. does the rate predict where web saw the splits?
        print("\n3. does that rate predict web's observed split points?")
        if rate == 0:
            skip("R3", "no disagreement on realistic values, so the rate "
                       "cannot be compared with web's split points")
        else:
            print("      assuming ~%d placements scored per move:"
                  % PLACEMENTS_PER_MOVE)
            consistent = 0
            for seed, split in sorted(WEB_SPLITS.items()):
                scores = split * PLACEMENTS_PER_MOVE
                exp = scores * rate
                print("        seed %d split at move %5d -> ~%7d scores -> "
                      "%.2f expected disagreements" % (seed, split, scores, exp))
                if 0.1 <= exp <= 20:
                    consistent += 1
            if consistent == len(WEB_SPLITS):
                fail("R3", "the measured disagreement rate predicts ~0.4 to ~2 "
                           "flipped roundings per game at exactly the move "
                           "counts where web observed the split (%r). That is "
                           "the right ORDER OF MAGNITUDE, which makes this a "
                           "live candidate for the endgame divergence -- not a "
                           "proof, but it is the first mechanism measured to "
                           "fit. Testable prediction below."
                     % {k: v for k, v in sorted(WEB_SPLITS.items())})
            else:
                ok("the rate does not fit web's split points (%d of %d in a "
                   "plausible range), so this mechanism is probably not the "
                   "cause" % (consistent, len(WEB_SPLITS)))

        # --- 4. direction: web says JS consistently survives LONGER
        print("\n4. direction of the bias vs web's observation")
        higher = sum(1 for v, p, j in bad if j > p)
        lower = len(bad) - higher
        print("      on disagreements, JS rounds HIGHER (less negative) %d "
              "times, LOWER %d times" % (higher, lower))
        if bad and higher == len(bad):
            print("      web observed: JS survives longer on ALL THREE seeds "
                  "(+30, +1, +4 pieces) -- a one-directional asymmetry")
            fail("R4", "the bias is ONE-DIRECTIONAL: on every disagreement JS "
                       "rounds a negative score UP (toward zero), i.e. it makes "
                       "that placement look BETTER than Python does. web "
                       "independently observed that its JS run survives longer "
                       "on all three seeds. A symmetric floating-point error "
                       "would not produce a consistent direction; this "
                       "mechanism does, and it matches. That agreement between "
                       "two independent observations is the strongest evidence "
                       "here.")
        elif bad:
            ok("the bias is not one-directional (%d up, %d down), which does "
               "NOT match web's consistent one-way asymmetry -- weakens this "
               "mechanism as the explanation" % (higher, lower))

        # --- 5. the falsification condition, stated up front
        print("\n5. how to confirm or kill this (per the team rule)")
        print("      Make the two sides round identically -- easiest is for JS")
        print("      to stop rounding and compare with an explicit epsilon, or")
        print("      to implement ties-to-even. IF THIS IS THE CAUSE:")
        print("        - seed 900009 must then match Python for the FULL game,")
        print("          not just 11,700 moves")
        print("        - the same must hold for seeds 900001 and 900004")
        print("        - and the one-directional survival bias must vanish")
        print("      IF ANY OF THOSE STILL FAILS, this is not the cause and I")
        print("      am wrong -- the next suspect is the dot-product")
        print("      accumulation order (numpy BLAS vs a scalar JS loop),")
        print("      which rl already documented as a real source of last-bit")
        print("      disagreement.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return report()


def report():
    print("\n" + "=" * 74)
    print("PASS %d   FAIL %d   SKIP %d" % (len(PASSED), len(FAILED),
                                           len(SKIPPED)))
    if SKIPPED:
        print("skipped (NOT verified, not passed):")
        for t, m in SKIPPED:
            print("  [%s] %s" % (t, m.splitlines()[0]))
    if FAILED:
        print("FINDINGS:")
        for t, m in FAILED:
            print("  [%s] %s" % (t, m.splitlines()[0]))
        return 1
    if not PASSED:
        # A test that verified NOTHING must not print a sentence that reads as
        # a pass. This line used to say "the two implementations agree" while
        # the counters right above it said PASS 0 / SKIP 1 -- the team's own
        # rule ("a skip is not a pass") broken by a checker-owned tool.
        print("RESULT: NOTHING WAS VERIFIED -- %d check(s) skipped, 0 passed. "
              "Do NOT read this as agreement." % len(SKIPPED))
        return 1
    print("RESULT: the tie-break rules agree (%d passed, %d skipped)."
          % (len(PASSED), len(SKIPPED)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
