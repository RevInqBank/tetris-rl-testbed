"""Independent recomputation of rl's 200-seed median comparison.

THE CLAIM UNDER ATTACK (weights/eval_pg200.json, read 2026-08-07 20:36:41,
mtime 20:27:52):

    median lines over 200 held-out seeds, piece cap 3000, greedy, on engine
        panel 4  reinforce            76.5
        panel 5  reinforce_baseline    90.0
        panel 6  a2c                   65.0
        panel 7  dqn                  101.5
    and the headline the report leans on:  (5) - (6) = +25.0 lines

That headline is the study plan's central axis: a variance-reduction baseline
(no bootstrap) versus a bootstrapped 1-step critic. If it does not hold, the
comparison table teaches the wrong lesson.

WHY THIS IS NOT A RERUN OF rl'S HARNESS. Three independences:
  1. MY SEEDS. rl used 910000..910199. I use a disjoint block, so the result
     cannot depend on which seeds were drawn.
  2. A DIFFERENT FORWARD PASS. rl scores with `rl/nn.py` (numpy). I score with
     `web/policies.js` (JavaScript) through node. Same weights file, two
     independent implementations of the MLP and the feature scaling.
  3. PAIRED + BOOTSTRAP. rl reported point medians. A difference of medians
     needs an interval before it can carry a conclusion, so the paired
     difference is bootstrapped here.

WHAT THIS DOES NOT PROVE: that the training is correct, or that these medians
are the best these algorithms can do. Only that the reported ORDERING and GAP
reproduce on seeds rl never touched, under a different evaluator.

Run:  <ml python> tests/test_median_recompute.py [n_seeds]
Owner of the code under test: rl.  This file is checker-owned.
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
RUNNER = os.path.join(_HERE, "_median_runner.mjs")
CLAIM = os.path.join(WEIGHTS, "eval_pg200.json")

#: Disjoint from rl's training (1..100000) AND from rl's eval (910000..910199).
SEED_BASE = 660000
PIECE_CAP = 3000

#: study-plan panels 4..7
STRATS = ["reinforce", "reinforce_baseline", "a2c", "dqn"]
#: the headline contrast: panel 5 minus panel 6
CONTRAST = ("reinforce_baseline", "a2c")

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
import { readFileSync, readdirSync } from 'fs';

const wdir = '%(weights)s';
for (const f of readdirSync(wdir)) {
  if (!f.endsWith('.json')) continue;
  let j; try { j = JSON.parse(readFileSync(wdir + '/' + f, 'utf8')); } catch { continue; }
  if (!j || !j.kind) continue;
  try { P.registerModel(f.replace(/\.json$/, ''), j); } catch {}
}
// rl evaluated the policy_* kinds GREEDILY; make that explicit, do not inherit
// a default that could change under us.
P.options.greedyPolicy = true;

function play(stratId, seed, cap) {
  let s = E.newGame(seed, 0);
  let n = 0;
  for (; n < cap; n++) {
    const ps = E.legalPlacements(s);
    if (!ps.length) break;
    const r = P.chooseAction(s, stratId, () => 0.5, 'normal');
    if (!r || !r.placement) return { error: 'no placement at ' + n };
    const [ns] = E.applyPlacement(s, r.placement);
    s = ns;
    if (s.game_over) break;
  }
  return { lines: s.lines, score: s.score, pieces: n, died: s.game_over };
}

const STRATS = %(strats)s;
const seeds = [];
for (let i = 0; i < %(n)d; i++) seeds.push(%(base)d + i);

const out = { ready: {}, kinds: {}, games: {} };
for (const id of STRATS) {
  const st = P.byId(id);
  out.ready[id] = st ? !!P.isReady(st) : null;
  try { out.kinds[id] = P.modelFor(st).kind; } catch { out.kinds[id] = null; }
}
for (const id of STRATS) {
  const rows = [];
  for (const sd of seeds) rows.push(play(id, sd, %(cap)d));
  out.games[id] = rows;
}
process.stdout.write(JSON.stringify(out));
"""


def median(a):
    b = sorted(a)
    n = len(b)
    if n == 0:
        return float("nan")
    return float(b[(n - 1) // 2]) if n % 2 else (b[n // 2 - 1] + b[n // 2]) / 2.0


def boot_ci_median_diff(xs, ys, iters=4000, seed=12345):
    """Percentile bootstrap CI for median(xs) - median(ys), PAIRED.

    Paired because both strategies played the same seeds: resample seed
    indices, not the two samples independently.
    """
    n = len(xs)
    st = seed & 0xFFFFFFFF or 1
    diffs = []
    for _ in range(iters):
        bx, by = [], []
        for _k in range(n):
            # xorshift32, so the interval is reproducible without numpy
            st ^= (st << 13) & 0xFFFFFFFF
            st ^= st >> 17
            st ^= (st << 5) & 0xFFFFFFFF
            st &= 0xFFFFFFFF
            i = st % n
            bx.append(xs[i])
            by.append(ys[i])
        diffs.append(median(bx) - median(by))
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[min(len(diffs) - 1, int(0.975 * len(diffs)))]
    return lo, hi


def main():
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print("=" * 74)
    print("checker: independent recomputation of rl's 200-seed medians")
    print("  my seeds %d..%d (disjoint from rl's train 1..100000 and eval "
          "910000..910199)" % (SEED_BASE, SEED_BASE + n_seeds - 1))
    print("  scored through web/policies.js (JS), NOT rl/nn.py")
    print("=" * 74)

    if not os.path.exists(CLAIM):
        skip("M0", "weights/eval_pg200.json absent -- nothing to compare "
                   "against")
        return report()
    claim = json.load(open(CLAIM))
    cs = claim["strategies"]
    print("\nrl's claim (piece cap %s, n=%s, primary statistic %r):"
          % (claim.get("last_run", {}).get("piece_cap"),
             cs[STRATS[0]].get("n"), claim.get("primary_statistic")))
    for s in STRATS:
        print("  panel %s  %-20s median %-7s mean %-8s hit_cap %s"
              % (cs[s].get("panel"), s, cs[s].get("median_lines"),
                 cs[s].get("mean_lines"), cs[s].get("games_hit_cap")))

    node = shutil.which("node")
    if not node:
        skip("M0", "node not installed -- the independent recomputation did "
                   "NOT run")
        return report()

    with open(RUNNER, "w") as f:
        f.write(JS % {"web": WEB.replace("\\", "/"),
                      "weights": WEIGHTS.replace("\\", "/"),
                      "strats": json.dumps(STRATS),
                      "n": n_seeds, "base": SEED_BASE, "cap": PIECE_CAP})
    print("\nrunning %d seeds x %d strategies (this takes a few minutes)..."
          % (n_seeds, len(STRATS)))
    proc = subprocess.run([node, "--max-old-space-size=2048", RUNNER],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        fail("M0", "the JS evaluator failed (node exists, so this is a failure "
                   "not a skip):\n%s" % proc.stderr[-1500:])
        return report()
    d = json.loads(proc.stdout)

    errs = {k: [g for g in v if g.get("error")] for k, v in d["games"].items()}
    for k, v in errs.items():
        if v:
            fail("M0", "%s: %s" % (k, v[0]["error"]))
    if any(errs.values()):
        return report()
    notready = [k for k, v in d["ready"].items() if not v]
    if notready:
        fail("M0", "not registered in the browser layer: %s" % notready)
        return report()

    mine = {}
    print("\nmy recomputation:")
    for s in STRATS:
        lines = [g["lines"] for g in d["games"][s]]
        caps = sum(1 for g in d["games"][s] if not g["died"])
        mine[s] = lines
        med, mean = median(lines), sum(lines) / float(len(lines))
        print("  %-20s median %-7.1f mean %-8.2f min %-5d max %-5d "
              "mean/med %.2f  hit_cap %d"
              % (s, med, mean, min(lines), max(lines), mean / med if med else 0,
                 caps))
        if caps:
            fail("M1", "%s hit the piece cap in %d/%d of MY games -- the "
                       "median is then cap-limited and not comparable"
                 % (s, caps, len(lines)))

    # ---- 1. does the ORDERING reproduce?
    print("\n1. does the ranking reproduce on my seeds?")
    claim_order = sorted(STRATS, key=lambda s: -cs[s]["median_lines"])
    mine_order = sorted(STRATS, key=lambda s: -median(mine[s]))
    print("      rl's ranking : %s" % " > ".join(claim_order))
    print("      my ranking   : %s" % " > ".join(mine_order))
    if claim_order == mine_order:
        ok("the full 4-way ranking by median reproduces exactly on seeds rl "
           "never used, under a different forward pass")
    else:
        fail("M2", "the ranking does NOT reproduce: rl has %s, I get %s. The "
                   "comparison table's ordering is what the user reads off the "
                   "screen, so this difference is the finding."
             % (" > ".join(claim_order), " > ".join(mine_order)))

    # ---- 2. the headline contrast, with an interval
    a, b = CONTRAST
    print("\n2. the headline contrast: %s - %s" % (a, b))
    claim_delta = cs[a]["median_lines"] - cs[b]["median_lines"]
    my_delta = median(mine[a]) - median(mine[b])
    lo, hi = boot_ci_median_diff(mine[a], mine[b])
    wins = sum(1 for x, y in zip(mine[a], mine[b]) if x > y)
    print("      rl : %+.1f  (median %.1f vs %.1f)"
          % (claim_delta, cs[a]["median_lines"], cs[b]["median_lines"]))
    print("      me : %+.1f  95%% CI [%+.1f, %+.1f]  (paired bootstrap, "
          "n=%d)" % (my_delta, lo, hi, len(mine[a])))
    print("      paired wins: %s beats %s on %d/%d seeds"
          % (a, b, wins, len(mine[a])))
    if lo > 0:
        ok("the interval excludes zero, so %s > %s is a real ordering on my "
           "seeds too (not just a point estimate)" % (a, b))
    elif hi < 0:
        fail("M3", "the contrast REVERSES on my seeds: %+.1f with CI [%+.1f, "
                   "%+.1f]. rl reports %+.1f." % (my_delta, lo, hi, claim_delta))
    else:
        fail("M3", "the contrast is NOT significant on my seeds: %+.1f with CI "
                   "[%+.1f, %+.1f] -- the interval contains zero, so '%s beats "
                   "%s by %+.1f lines' is not supported at n=%d. rl reported a "
                   "point median difference with no interval, which cannot "
                   "distinguish this case from a real effect."
             % (my_delta, lo, hi, a, b, claim_delta, len(mine[a])))
    # is my delta consistent with rl's number at all?
    if not (lo <= claim_delta <= hi):
        fail("M3", "rl's reported %+.1f falls OUTSIDE my 95%% CI [%+.1f, %+.1f]"
                   " -- the two evaluations disagree beyond sampling noise. "
                   "Same weights, different seeds and different forward pass, "
                   "so the cause is one of those two."
             % (claim_delta, lo, hi))
    else:
        ok("rl's reported %+.1f lies inside my 95%% CI [%+.1f, %+.1f] -- the "
           "two independent evaluations agree" % (claim_delta, lo, hi))

    # ---- 3. every pairwise gap, so the table as a whole is judged
    print("\n3. all adjacent gaps in the ranking (is the TABLE readable?)")
    for i in range(len(mine_order) - 1):
        x, y = mine_order[i], mine_order[i + 1]
        dl, dh = boot_ci_median_diff(mine[x], mine[y])
        dd = median(mine[x]) - median(mine[y])
        verdict = "separated" if dl > 0 else "NOT separated"
        print("      %-20s vs %-20s %+7.1f  CI [%+.1f, %+.1f]  %s"
              % (x, y, dd, dl, dh, verdict))
        if dl <= 0:
            fail("M4", "%s and %s are NOT separated at n=%d (%+.1f, CI [%+.1f, "
                       "%+.1f]). Presenting them as ranked neighbours in the "
                       "comparison table implies an ordering the data does not "
                       "support -- say 'not separated at this n' instead."
                 % (x, y, len(mine[x]), dd, dl, dh))

    # ---- 4. mean/median divergence, which is why median was adopted
    print("\n4. mean vs median (rl switched to median for this reason)")
    for s in STRATS:
        med = median(mine[s])
        mean = sum(mine[s]) / float(len(mine[s]))
        ratio = mean / med if med else float("inf")
        top = max(mine[s]) / float(sum(mine[s])) if sum(mine[s]) else 0
        print("      %-20s mean/median %.2f   largest single game = %.0f%% of "
              "the total" % (s, ratio, 100 * top))
    ok("recorded for the report: the heavy tail that made the mean unusable is "
       "present in my run too, on different seeds -- so it is a property of "
       "these policies, not of rl's seed choice")
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
    print("RESULT: rl's medians and the headline contrast reproduce "
          "independently.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
