"""POLICY-layer parity: does web/policies.js CHOOSE the same move as rl?

This closes a coverage gap I had to admit in writing. `test_parity_coverage.py`
compares 146,554 move records between the Python and JS ENGINES and finds them
identical -- and that test is structurally incapable of catching a policy bug,
because both engines agree perfectly about what a placement DOES. What was never
compared move by move is which placement gets CHOSEN.

That gap was not hypothetical. web found a real divergence there by bisecting
against rl's reference trajectory: seed 900009 matched for 11,700 moves and then
split in the last 3. My green parity run said nothing about it. The cause turned
out to be a DEATH penalty web applied in `scoreAll` that rl's greedy path does
not -- invisible for 11,700 moves because a game-over placement only exists once
the stack nearly touches the ceiling.

So this test replays rl's reference trajectory through `web/policies.js` and
compares, at every checkpoint, the four things that pin down the CHOICE:

    board_hash      the position before the move (22 rows)
    n_legal         how many placements were offered
    chose_rot       the rotation chosen
    chose_left_col  the leftmost occupied column of the chosen placement

Reference: weights/trace_ref_cem_score.json, produced by rl's engine harness.
Its `tie_rule` field is authoritative and this test asserts both sides honour
it. Note rl's own `supersedes` note: the earlier 9-decimal-rounding version of
that file was discarded because Python's round() and JS's Math.round() disagree
on negatives -- the finding this file's sibling (test_round9_parity.py) measured.

WHAT THIS DOES NOT PROVE: agreement at 25-move checkpoints, not at every move.
A divergence that appears and self-corrects inside one interval is invisible
here. It also only covers the policy in `policy` below, on three seeds.

Run:  python3 tests/test_policy_layer_parity.py
Owners: web (policies.js), rl (the reference trace). This file is checker-owned.
"""

import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

WEB = os.path.join(_ROOT, "web")
WEIGHTS = os.path.join(_ROOT, "weights")
RUNNER = os.path.join(_HERE, "_policy_layer_runner.mjs")
REF = os.path.join(WEIGHTS, "trace_ref_cem_score.json")

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


JS = r"""
import * as E from '%(web)s/engine.js';
import * as P from '%(web)s/policies.js';
import { readFileSync, readdirSync, writeFileSync } from 'fs';

const wdir = '%(weights)s';
for (const f of readdirSync(wdir)) {
  if (!f.endsWith('.json')) continue;
  let j; try { j = JSON.parse(readFileSync(wdir + '/' + f, 'utf8')); } catch { continue; }
  if (!j || !j.kind) continue;
  try { P.registerModel(f.replace(/\.json$/, ''), j); } catch {}
}
P.options.greedyPolicy = true;

const ref = JSON.parse(readFileSync('%(ref)s', 'utf8'));
const stratId = '%(strat)s';
const every = ref.checkpoint_every;
const out = {};

for (const seedStr of Object.keys(ref.seeds)) {
  const seed = parseInt(seedStr, 10);
  const want = ref.seeds[seedStr].checkpoints;
  const lastPiece = want[want.length - 1].piece;
  let s = E.newGame(seed, 0);
  const got = [];
  /* rl samples every `every` moves AND appends one final off-grid checkpoint
     at the last piece of the game, so the sample set is
     {0, every, 2*every, ...} U {last}. Take the pieces to record straight from
     the reference instead of recomputing the rule -- that way the comparison
     cannot drift from how the reference was built. */
  const marks = new Set(want.map(c => c.piece));
  for (let piece = 0; piece <= lastPiece; piece++) {
    const ps = E.legalPlacements(s);
    if (!ps.length) break;
    const r = P.chooseAction(s, stratId, () => 0.5, 'normal');
    if (!r || !r.placement) { out[seedStr] = {error: 'no placement at ' + piece}; break; }
    // record BEFORE the move, matching ref.board_hash's definition
    if (marks.has(piece)) {
      got.push({piece,
                board_hash: E.boardHash(s.rows),
                lines: s.lines,
                chose_rot: r.placement[0],
                chose_left_col: E.placementLeftCol(r.placement),
                n_legal: ps.length});
    }
    const [ns] = E.applyPlacement(s, r.placement);
    s = ns;
    if (s.game_over) break;
  }
  /* rl's last checkpoint is a TERMINAL record: {piece, board_hash, lines,
     final:true, game_over:true} with no chosen move, because the game is over.
     Emit the same shape so the final BOARD HASH gets compared -- that is the
     strongest end-state agreement available. */
  if (!out[seedStr]) {
    got.push({piece: s.pieces, board_hash: E.boardHash(s.rows),
              lines: s.lines, final: true, game_over: !!s.game_over});
    out[seedStr] = {checkpoints: got, lines: s.lines, pieces: s.pieces};
  }
}
writeFileSync('%(outfile)s', JSON.stringify(out));
"""

FIELDS = ("board_hash", "lines", "chose_rot", "chose_left_col", "n_legal")


def main():
    print("=" * 74)
    print("checker: POLICY-layer parity (which move gets CHOSEN)")
    print("=" * 74)
    if not os.path.exists(REF):
        skip("P0", "weights/trace_ref_cem_score.json absent -- the policy layer "
                   "is UNVERIFIED (engine parity says nothing about it)")
        return report()
    if not shutil.which("node"):
        skip("P0", "node not installed -- the policy layer was NOT compared")
        return report()

    ref = json.load(open(REF, encoding="utf-8"))
    strat = os.path.splitext(os.path.basename(ref.get("policy", "")))[0]
    print("\n0. reference trajectory")
    print("      policy          : %s" % ref.get("policy"))
    print("      harness         : %s" % ref.get("harness"))
    print("      difficulty      : %s" % ref.get("difficulty"))
    print("      checkpoint_every: %s" % ref.get("checkpoint_every"))
    print("      tie_rule        : %s" % ref.get("tie_rule"))
    if ref.get("supersedes"):
        print("      supersedes      : %s" % ref["supersedes"])
    print("      seeds           : %s" % ", ".join(sorted(ref["seeds"])))

    # the reference declares its own tie rule -- assert BOTH sides honour it
    tr = (ref.get("tie_rule") or "")
    pol = open(os.path.join(WEB, "policies.js"), encoding="utf-8").read()
    feat_p = os.path.join(_ROOT, "rl", "features.py")
    feat = open(feat_p, encoding="utf-8").read() if os.path.exists(feat_p) else ""
    if "NO ROUNDING" in tr.upper() or "1e-9" in tr:
        js_eps = "SCORE_EPS" in pol and "> scores[best] + SCORE_EPS" in pol
        py_eps = "SCORE_EPS" in feat and "> best_v + SCORE_EPS" in feat
        if js_eps and py_eps:
            ok("the reference declares an epsilon tie rule and BOTH "
               "implementations use it (`v > best + SCORE_EPS`), so a "
               "mismatch below would be a real choice difference and not a "
               "tie-break artefact")
        else:
            fail("P1", "the reference declares tie_rule=%r but js_eps=%s / "
                       "py_eps=%s -- one side is not using the declared rule, "
                       "so any comparison below is against a moving target"
                 % (tr[:60], js_eps, py_eps))

    outfile = os.path.join(_HERE, "_policy_layer_out.json")
    with open(RUNNER, "w") as f:
        f.write(JS % {"web": WEB.replace("\\", "/"),
                      "weights": WEIGHTS.replace("\\", "/"),
                      "ref": REF.replace("\\", "/"),
                      "strat": strat,
                      "outfile": outfile.replace("\\", "/")})
    print("\n1. replaying the reference seeds through web/policies.js ...")
    proc = subprocess.run(["node", "--max-old-space-size=2048", RUNNER],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        fail("P0", "the JS replay failed (node exists, so a failure not a "
                   "skip):\n%s" % proc.stderr[-1500:])
        return report()
    got = json.load(open(outfile))

    total_cp = 0
    for seed in sorted(ref["seeds"]):
        want = ref["seeds"][seed]["checkpoints"]
        g = got.get(seed) or {}
        if g.get("error"):
            fail("P2", "seed %s: JS replay errored: %s" % (seed, g["error"]))
            continue
        mine = g.get("checkpoints") or []
        n = min(len(want), len(mine))
        first_bad = None
        for i in range(n):
            # a terminal record carries no chosen move, so compare only the
            # fields it actually has
            flds = (("board_hash", "lines", "game_over", "piece")
                    if want[i].get("final") else FIELDS)
            if bool(want[i].get("final")) != bool(mine[i].get("final")):
                first_bad = (i, "final", want[i], mine[i])
                break
            for fld in flds:
                if want[i].get(fld) != mine[i].get(fld):
                    first_bad = (i, fld, want[i], mine[i])
                    break
            if first_bad:
                break
        total_cp += n
        if first_bad is None and len(mine) == len(want):
            ok("seed %s: all %d checkpoints identical, INCLUDING the terminal "
               "record (final board_hash %s at piece %s) -- the JS policy chose "
               "the same move as rl at every sampled position and the game "
               "ended in the same position"
               % (seed, len(want), want[-1].get("board_hash"),
                  want[-1].get("piece")))
        elif first_bad is None:
            fail("P2", "seed %s: the %d compared checkpoints agree, but the "
                       "trajectories are different LENGTHS (rl %d, JS %d). The "
                       "JS run ended at a different point, so it diverged after "
                       "the last common checkpoint."
                 % (seed, n, len(want), len(mine)))
        else:
            i, fld, w, m = first_bad
            fail("P2", "seed %s: FIRST DIVERGENCE at checkpoint %d (piece %s), "
                       "field %r: rl=%r JS=%r. Full records rl=%r JS=%r. The "
                       "engines agree on what placements do, so this is the "
                       "POLICY choosing differently -- the layer engine parity "
                       "cannot see."
                 % (seed, i, w.get("piece"), fld, w.get(fld), m.get(fld), w, m))
        # line totals are a coarse cross-check on the whole game
        if "lines" in ref["seeds"][seed] and "lines" in g:
            rl_lines, js_lines = ref["seeds"][seed]["lines"], g["lines"]
            if rl_lines != js_lines:
                fail("P3", "seed %s: final line totals differ, rl=%d JS=%d "
                           "(%+d). Even if the sampled checkpoints matched, the "
                           "games did not end the same way."
                     % (seed, rl_lines, js_lines, js_lines - rl_lines))
            else:
                ok("seed %s: final line total matches too (%d) -- agreement "
                   "holds to the end of the game, not just to the last "
                   "checkpoint" % (seed, rl_lines))

    print("\n2. scope of this check")
    print("      %d checkpoints compared across %d seeds, sampled every %s "
          "moves" % (total_cp, len(ref["seeds"]), ref.get("checkpoint_every")))
    print("      NOT covered: divergence that appears and self-corrects inside")
    print("      one 25-move interval; policies other than %s; seeds other" % strat)
    print("      than the three in the reference file.")
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
            print("  [%s] %s" % (t, m))
        return 1
    if not PASSED:
        print("RESULT: NOTHING WAS VERIFIED -- %d skipped, 0 passed. Do NOT "
              "read this as agreement." % len(SKIPPED))
        return 1
    print("RESULT: the JS policy layer chooses the same moves as rl.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
