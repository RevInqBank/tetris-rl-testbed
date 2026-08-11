"""Does the CEM search distribution collapse, and if so is the compute after
that point doing anything?

The lead reported that `sigma_norm` collapses to 0 during CEM training and that
the generations after that "do not learn". This file measures it, because the
claim needs three separate things established, not one:

  1. WHEN does the search distribution degenerate? (sigma_norm -> ~0)
  2. Do the generations after that point still report IMPROVING fitness? If so,
     is that improvement real, or is it noise from re-drawing the evaluation
     seeds every generation on a frozen policy?
  3. Does any published artefact take its number from the argmax generation? If
     it does, it is reporting a noise peak; if it takes the distribution mean,
     it is not, and that deserves saying.

This matters because it attacks a claim about LEARNING ITSELF, not about
performance. "N generations of training" is the headline for the CEM panels;
if a quarter of N could not move the weights, N overstates the work done.

Read: weights/curve_cem_score.jsonl (mtime recorded at run time -- this file
is APPENDED TO while training runs, so the numbers grow between runs).

Run:  python3 tests/test_cem_variance_collapse.py
Owner of the code under test: rl.  This file is checker-owned.
"""

import json
import os
import statistics as st
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
WEIGHTS = os.path.join(_ROOT, "weights")
import glob
CURVES = sorted(glob.glob(os.path.join(WEIGHTS, "curve_*.jsonl")))

#: sigma_norm below this is a degenerate distribution: candidates drawn from it
#: differ from the mean by ~1e-6 in weight space, which cannot change an argmax
#: over placements.
DEGENERATE = 1e-6

PASSED, FAILED, SKIPPED = [], [], []
#: z-scores of post-collapse noise peaks, one per collapsing curve
ZS = []


def ok(m):
    PASSED.append(m)
    print("  ok   %s" % m)


def fail(t, m):
    FAILED.append((t, m))
    print("  FAIL [%s] %s" % (t, m))


def skip(t, m):
    SKIPPED.append((t, m))
    print("  SKIP [%s] %s" % (t, m))


def main():
    print("=" * 74)
    print("checker: CEM search-variance collapse")
    print("  read at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 74)
    if not CURVES:
        skip("C0", "no weights/curve_*.jsonl -- nothing to measure")
        return report()
    print("  curves found: %s"
          % ", ".join(os.path.basename(c) for c in CURVES))
    rc = 0
    ZS.clear()
    for c in CURVES:
        rc |= one_curve(c)
    audit_selection(max(ZS) if ZS else float("nan"))
    return report()



def audit_selection(worst_z):
    """Run ONCE, not per curve: which artefacts publish the argmax generation?

    Kept separate from the per-curve analysis on purpose -- a weights file is
    not tied to any one curve here, so attributing a specific curve's z-score
    to it would overstate what is known. `worst_z` is only used to say how big
    a noise peak the argmax could be picking, in the run where a collapse WAS
    measured.
    """
    print("\n" + "-" * 74)
    print("SELECTION AUDIT (all weight files, once)")
    print("-" * 74)
    # ---- 3. does any artefact publish the argmax generation?
    checked = 0
    for fn in sorted(os.listdir(WEIGHTS)):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(WEIGHTS, fn)))
        except Exception:
            continue
        m = d.get("meta") or {}
        sel = m.get("selected")
        if sel is None:
            continue
        checked += 1
        if "best" in str(sel).lower() and "candidate" in str(sel).lower():
            fail("C3", "%s has meta.selected=%r -- it publishes the BEST "
                       "candidate. After the variance collapse the best "
                       "generation is a noise peak (%.2f sd above the mean of "
                       "its own series), so those weights are selected on "
                       "evaluation luck. Selecting the distribution mean avoids "
                       "this." % (fn, sel, worst_z))
        else:
            ok("%s has meta.selected=%r -- it does NOT take the argmax "
               "generation, so the noise peak is not published as the chosen "
               "policy. That is the right choice and it is worth saying so."
               % (fn, sel))
    if not checked:
        skip("C3", "no weight file declares meta.selected -- cannot tell which "
                   "generation was published")


    return 0


def one_curve(CURVE):
    print("\n" + "-" * 74)
    print("CURVE: %s" % os.path.basename(CURVE))
    print("-" * 74)
    print("  curve mtime %s"
          % time.strftime("%Y-%m-%d %H:%M:%S",
                          time.localtime(os.path.getmtime(CURVE))))

    rows = []
    for line in open(CURVE):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass          # a partially-written last line while training runs
    if len(rows) < 20:
        skip("C0", "%s: only %d generations logged -- too few to judge"
             % (os.path.basename(CURVE), len(rows)))
        return 0
    need = {"sigma_norm", "best_fitness", "mean_fitness", "elite_mean_fitness"}
    if not need.issubset(rows[0]):
        # Not every curve is a CEM run. Gradient training (a2c, dqn, reinforce)
        # has no search distribution, so there is no sigma to collapse -- that
        # is a SHAPE mismatch, not an unverified CEM run. Say which, because a
        # skip that reads like a coverage hole is worse than no skip at all.
        missing = sorted(need - set(rows[0]))
        gradientish = "sigma_norm" in missing and len(missing) == len(need)
        if gradientish:
            print("      not a CEM curve (no sigma_norm / fitness fields) -- "
                  "gradient training has no search distribution, so there is "
                  "nothing here that could collapse. Correctly out of scope, "
                  "NOT an unchecked CEM run.")
            ok("%s: out of scope by shape (gradient-training curve, no search "
               "distribution)" % os.path.basename(CURVE))
            return 0
        skip("C0", "%s is CEM-shaped but lacks %r, so its variance behaviour "
                   "was NOT checked" % (os.path.basename(CURVE), missing))
        return 0

    # A curve file can hold SEVERAL runs. Newer files stamp `run_id`; older ones
    # do not, and there the only signal is the `generation` counter RESETTING.
    # Worse, concurrent workers interleave their rows, so a naive sequential
    # read mixes runs together.
    #
    # This matters and I got it wrong first: I reported "617 of 1398
    # generations (44%) wasted" from a sequential read of a file that is
    # actually 9 interleaved segments. The COLLAPSE is real -- it is visible
    # inside single segments -- but that PERCENTAGE was an artefact of mixing
    # runs. Segment first, then measure.
    run_ids = [r.get("run_id") for r in rows]
    distinct = [x for x in dict.fromkeys(run_ids) if x is not None]
    segments = []
    if distinct:
        for rid_ in distinct:
            seg = [r for r in rows if r.get("run_id") == rid_]
            if len(seg) >= 20:
                segments.append((rid_, seg))
    else:
        # split wherever `generation` fails to increase
        start = 0
        gens = [r.get("generation") for r in rows]
        for i in range(1, len(rows)):
            if not (isinstance(gens[i], int) and isinstance(gens[i - 1], int)
                    and gens[i] > gens[i - 1]):
                if i - start >= 20:
                    segments.append(("gen-reset segment @%d" % start,
                                     rows[start:i]))
                start = i
        if len(rows) - start >= 20:
            segments.append(("gen-reset segment @%d" % start, rows[start:]))

    if not segments:
        skip("C0", "%s: no run segment of >=20 generations could be isolated "
                   "(file has %d rows, run_id present=%s) -- variance behaviour "
                   "NOT judged"
             % (os.path.basename(CURVE), len(rows), bool(distinct)))
        return 0
    if len(segments) > 1:
        print("      NOTE: this file contains %d run segment(s) of >=20 "
              "generations. Sigma from different runs must not be mixed, so "
              "each is judged on its own; the LONGEST is the headline."
              % len(segments))
        for label, seg in segments:
            sg = [r["sigma_norm"] for r in seg]
            print("        %-28s %4d rows  gens %s..%s  sigma min %.4g"
                  % (label, len(seg), seg[0].get("generation"),
                     seg[-1].get("generation"), min(sg)))
    rid, rows = max(segments, key=lambda kv: len(kv[1]))
    if len(segments) > 1:
        print("      analysing the longest segment: %s (%d generations)"
              % (rid, len(rows)))

    n = len(rows)
    sig = [r["sigma_norm"] for r in rows]
    best = [r["best_fitness"] for r in rows]
    g_first = rows[0].get("generation")
    g_last = rows[-1].get("generation")
    partial = isinstance(g_first, int) and g_first > 0
    print("\n1. does the search distribution degenerate?")
    if rid:
        print("      run_id             : %s" % rid)
    print("      generations logged : %d  (generation %s..%s)"
          % (n, g_first, g_last))
    if partial:
        print("      NOTE: the file starts at generation %s, so the first %s "
              "generations are NOT in it (rotated). Everything below describes "
              "the LOGGED WINDOW, not the whole run." % (g_first, g_first))
    print("      sigma_norm         : gen0 %.4g -> final %.4g (min %.4g)"
          % (sig[0], sig[-1], min(sig)))

    # first index from which sigma stays degenerate for the rest of the run
    coll = None
    for i in range(n):
        if all(s < DEGENERATE for s in sig[i:]):
            coll = i
            break
    if coll is None:
        ok("%s: sigma_norm never collapses across generations %s..%s "
           "(first %.4g, MIN %.4g, last %.4g; %d rows logged%s). This is what a "
           "variance floor looks like, and it is the falsification condition I "
           "stated for my own diagnosis: it held."
           % (os.path.basename(CURVE), g_first, g_last, sig[0], min(sig),
              sig[-1], n,
              "; earlier generations rotated out of the file" if partial
              else ""))
        return 0

    after = rows[coll:]
    frac = 100.0 * len(after) / n
    print("      degenerate from    : index %d (generation field %s) onward"
          % (coll, after[0].get("generation")))
    fail("C1", "the CEM search distribution COLLAPSES: sigma_norm stays below "
               "%g from generation %s onward, so %d of %d generations (%.0f%%) "
               "run with a degenerate distribution. Candidates drawn from it "
               "are numerically identical to the mean, so those generations "
               "cannot move the weights -- that fraction of the training "
               "compute bought nothing. There is no variance floor in the "
               "implementation."
         % (DEGENERATE, after[0].get("generation"), len(after), n, frac))

    # ---- 2. is the post-collapse "improvement" real?
    print("\n2. the fitness still moves after collapse. is that learning?")
    f = [r["best_fitness"] for r in after]
    mean_f, sd_f = st.mean(f), st.pstdev(f)
    rise = max(f) - f[0]
    print("      best_fitness after collapse: first %.3f -> max %.3f "
          "(apparent gain %+.3f)" % (f[0], max(f), rise))
    print("      distribution of those values: mean %.3f  sd %.3f  "
          "min %.3f  max %.3f" % (mean_f, sd_f, min(f), max(f)))
    z = (max(f) - mean_f) / sd_f if sd_f else float("inf")
    print("      the maximum is %.2f sd above the mean of the same series" % z)
    ZS.append(z)

    seeds_vary = False
    if "seeds" in after[0]:
        seeds_vary = any(after[i].get("seeds") != after[0].get("seeds")
                         for i in range(1, min(len(after), 20)))
    print("      evaluation seeds re-drawn every generation: %s" % seeds_vary)

    if seeds_vary and z < 3.0:
        fail("C2", "the apparent post-collapse gain of %+.3f is NOT learning. "
                   "The weights cannot move (section 1), the evaluation seeds "
                   "are re-drawn every generation, and the maximum of the "
                   "series is only %.2f sd above its own mean (sd %.3f) -- "
                   "exactly what re-evaluating ONE frozen policy on fresh seeds "
                   "looks like. Reporting the best generation's fitness as an "
                   "achieved score would be reporting a noise peak."
             % (rise, z, sd_f))
    elif not seeds_vary:
        ok("seeds are fixed across generations, so the post-collapse variation "
           "cannot be seed noise -- it needs another explanation")
    else:
        ok("the post-collapse maximum is %.2f sd above the mean, far enough out "
           "that seed noise alone is an unlikely explanation" % z)

    gb = max(best)
    gi = best.index(gb)
    where = "AFTER" if gi >= coll else "before"
    print("      global best %.3f first reached at index %d (generation %s) "
          "-- %s the collapse" % (gb, gi, rows[gi].get("generation"), where))
    if gi >= coll:
        print("      -> so the run's headline best came from the noisy region")

    # ---- 4. what is the honest generation count?
    print("\n4. the honest way to report the generation count")
    print("      total logged      : %d" % n)
    print("      effective (pre-collapse): %d" % coll)
    print("      wasted            : %d (%.0f%%)" % (len(after), frac))
    print("      -> report %d effective generations, or state the collapse. "
          "'%d generations' overstates the search that actually happened."
          % (coll, n))
    return 1


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
    print("RESULT: no variance collapse worth reporting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
