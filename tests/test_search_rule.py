"""Guards the ADOPTED results about 1-ply search, and the panel formula.

HISTORY, because it explains the shape of this file. I first reported that
1-ply search scored below plain greedy CEM and guessed the cause was the 'sum'
combining rule. rl replaced 'sum' with 'leaf'; I re-measured over 8 seeds and
the fix made it WORSE, so my hypothesis was wrong -- the combining rule was
never the cause. The lead then ADOPTED all three observations as results rather
than defects:

    1. replacing 'sum' with 'leaf' did not help
    2. 2-ply search over greedy-trained weights is a net loss (panel 8 now
       teaches "search is not always a win", with that scope stated on screen)
    3. hand-tuned Dellacherie beats CEM-learned weights ON SCORE

So this file's job flipped. It no longer flags those three as failures -- that
would be permanently red and would train the team to ignore the file. It now
asserts each adopted relationship STILL REPRODUCES, and fails only if one stops
holding, because at that moment the shipped narrative is what has gone stale.

It also still checks one genuine invariant: the formula shown on panel 8 must
match the rule the code dispatches.

Method: all strategies run through `web/policies.js` (the user-facing path) on
the same seeds with the same fixed move budget, so every policy places the same
number of pieces. SCORE, not lines -- lines saturate at this budget (no game
dies) and cannot separate the policies at all.

Run:  python3 tests/test_search_rule.py
Owners: rl (rule + weights), web (displayed formula). This file is checker-owned.
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
RUNNER = os.path.join(_HERE, "_search_rule_runner.mjs")

SEEDS = [20260807, 555001, 987654, 111111, 222222, 333333, 444444, 777777]
MOVES = 900

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
P.options.greedyPolicy = true;

function play(stratId, seed, max) {
  let s = E.newGame(seed, 0);
  let i = 0;
  for (; i < max; i++) {
    const ps = E.legalPlacements(s);
    if (!ps.length) break;
    const r = P.chooseAction(s, stratId, () => 0.5, 'normal');
    if (!r || !r.placement) return { error: 'no placement at ' + i };
    const [ns] = E.applyPlacement(s, r.placement);
    s = ns;
    if (s.game_over) break;
  }
  return { lines: s.lines, score: s.score, pieces: i, died: s.game_over };
}

const STRATS = %(strats)s;
const out = { rules: {}, games: {}, ready: {} };
for (const id of STRATS) {
  const st = P.byId(id);
  if (!st) { out.ready[id] = null; continue; }
  out.ready[id] = !!P.isReady(st);
  const m = (() => { try { return P.modelFor(st); } catch { return null; } })();
  /* Mirror the UI's OWN precedence (ui.js: meta.update_formula || s.formula).
     Reading st.formula alone would test a string the user never sees. */
  const meta = (m && m.meta) || {};
  const shownRaw = meta.update_formula || st.formula || '';
  out.rules[id] = {
    declared: m ? (m.searchRule || null) : null,
    source: meta.update_formula ? 'meta.update_formula' : 'static st.formula',
    shown: shownRaw.split('\n').map(x => x.trim()).filter(Boolean),
    staticFallback: (st.formula || '').split('\n').map(x => x.trim()).filter(Boolean),
  };
}
for (const id of STRATS)
  for (const sd of %(seeds)s)
    out.games[id + '|' + sd] = play(id, sd, %(moves)d);
process.stdout.write(JSON.stringify(out));
"""


def median(a):
    b = sorted(a)
    n = len(b)
    return float(b[(n - 1) // 2]) if n % 2 else (b[n // 2 - 1] + b[n // 2]) / 2.0


def has_parent_term(text):
    """The two rules differ by exactly one thing: a term ADDED to the max. So
    look for a '+' that comes BEFORE the 'max', not merely any '+'."""
    plus, mx = text.find("+"), text.find("max")
    return plus != -1 and mx != -1 and plus < mx


def check_formula(d):
    print("\n1. does the DISPLAYED formula match the rule the code runs?")
    for sid in ("search_1ply", "search_1ply_sum"):
        info = d["rules"].get(sid) or {}
        declared, shown = info.get("declared"), info.get("shown") or []
        print("      %-16s declared rule=%-5s (formula source: %s)"
              % (sid, declared, info.get("source")))
        for ln in shown:
            print("                       shown: %s" % ln)
        if declared is None:
            skip("S1", "%s has no declared search rule" % sid)
            continue
        joined = " ".join(shown)
        annotated = "폐기" in joined or "superseded" in joined.lower()
        parent = has_parent_term(joined)
        if declared == "leaf" and parent and not annotated:
            fail("S1", "%s runs rule='leaf' (max over p2 only) but the screen "
                       "shows a parent term before the max: %r. This panel is a "
                       "teaching device -- the formula on screen IS the product."
                 % (sid, shown[0] if shown else ""))
        elif declared == "sum" and not parent:
            fail("S1", "%s runs rule='sum' but the screen shows no parent term: "
                       "%r" % (sid, shown[0] if shown else ""))
        else:
            ok("%s: rule=%r and the formula the user sees agrees (parent "
               "term=%s, marked superseded=%s), both from %s so they cannot "
               "drift apart in separate edits"
               % (sid, declared, parent, annotated, info.get("source")))
        fb = " ".join(info.get("staticFallback") or [])
        if fb and info.get("source") == "meta.update_formula":
            fb_parent = has_parent_term(fb)
            fb_annot = "폐기" in fb or "현재" in fb
            if declared == "leaf" and fb_parent and not fb_annot:
                fail("S1", "%s's STATIC fallback still shows the sum shape. It "
                           "is dormant while meta.update_formula exists, but it "
                           "is what the screen falls back to if rl drops that "
                           "field -- the same defect one step back." % sid)
            else:
                ok("%s's static fallback is consistent with rule=%r too, so "
                   "losing meta.update_formula would not resurrect the bug"
                   % (sid, declared))


def main():
    print("=" * 74)
    print("checker: adopted results about 1-ply search, and the panel formula")
    print("=" * 74)
    if not shutil.which("node"):
        skip("S0", "node not installed -- NOTHING here was measured")
        return report()

    strats = ["cem_linear", "search_1ply", "search_1ply_sum", "dellacherie"]
    with open(RUNNER, "w") as f:
        f.write(JS % {"web": WEB.replace("\\", "/"),
                      "weights": WEIGHTS.replace("\\", "/"),
                      "strats": json.dumps(strats),
                      "seeds": json.dumps(SEEDS), "moves": MOVES})
    proc = subprocess.run(["node", RUNNER], capture_output=True, text=True)
    if proc.returncode != 0:
        fail("S0", "the JS harness failed (node exists, so a failure not a "
                   "skip):\n%s" % proc.stderr[-1500:])
        return report()
    d = json.loads(proc.stdout)

    errs = {k: v["error"] for k, v in d["games"].items() if v.get("error")}
    for k, v in list(errs.items())[:3]:
        fail("S0", "%s: %s" % (k, v))
    if errs:
        return report()

    check_formula(d)

    print("\n2. score over %d seeds, %d-move budget, normal mode"
          % (len(SEEDS), MOVES))
    sc = {sid: [d["games"]["%s|%d" % (sid, s)]["score"] for s in SEEDS]
          for sid in strats}
    all_capped = all(not d["games"]["%s|%d" % (sid, s)]["died"]
                     for sid in strats for s in SEEDS)
    print("      every game survives the budget: %s -- so all policies place "
          "the same number of pieces and score is a FAIR comparison, while "
          "lines saturate and separate nothing" % all_capped)
    for sid in strats:
        print("      %-16s median %-9s min %-9s max %s"
              % (sid, median(sc[sid]), min(sc[sid]), max(sc[sid])))

    base = sc["cem_linear"]
    leaf, summ = sc["search_1ply"], sc["search_1ply_sum"]

    # ---- adopted result 1
    print("\n3. ADOPTED RESULT: replacing 'sum' with 'leaf' did not help")
    wins = sum(1 for a, b in zip(leaf, summ) if a > b)
    dl = median(leaf) - median(summ)
    print("      leaf beats sum on %d/%d seeds, median %+d"
          % (wins, len(SEEDS), dl))
    if wins <= len(SEEDS) / 2.0:
        ok("reproduces: 'leaf' is no better than the 'sum' rule it replaced. "
           "The reasoning for the change was sound (summing two depths "
           "double-counts the middle node), so this is the evidence that the "
           "combining rule was never the cause of the deficit below.")
    else:
        fail("S2", "THE ADOPTED RESULT NO LONGER REPRODUCES: 'leaf' now beats "
                   "'sum' on %d/%d seeds (median %+d). Something changed in the "
                   "weights, the search code or the engine, and the reported "
                   "narrative must be re-examined before it ships."
             % (wins, len(SEEDS), dl))

    # ---- adopted result 2
    print("\n4. ADOPTED RESULT: 2-ply search over greedy-trained weights loses")
    for sid in ("search_1ply", "search_1ply_sum"):
        w = sum(1 for a, b in zip(sc[sid], base) if a > b)
        dd = median(sc[sid]) - median(base)
        print("      %-16s beats cem_linear on %d/%d seeds, median %+d"
              % (sid, w, len(SEEDS), dd))
        if w <= len(SEEDS) / 2.0:
            ok("reproduces: %s does not beat greedy cem_linear. Scope, as "
               "stated on the panel: what was measured is a value function "
               "trained for GREEDY one-step use and then wrapped in a 2-ply "
               "search -- not 'search is useless'." % sid)
        else:
            fail("S3", "THE ADOPTED RESULT NO LONGER REPRODUCES: %s now BEATS "
                       "greedy cem_linear on %d/%d seeds (median %+d). Panel 8 "
                       "currently teaches the user that search does not help "
                       "here; if that changed, the panel text and the report "
                       "are both wrong now." % (sid, w, len(SEEDS), dd))

    # ---- adopted result 3
    print("\n5. ADOPTED RESULT: hand weights beat CEM-learned weights on SCORE")
    dw = sum(1 for a, b in zip(sc["dellacherie"], base) if a > b)
    dd = median(sc["dellacherie"]) - median(base)
    print("      dellacherie beats cem_linear on %d/%d seeds, median %+d"
          % (dw, len(SEEDS), dd))
    if dw > len(SEEDS) / 2.0:
        ok("reproduces: hand-tuned Dellacherie still beats CEM-learned weights "
           "on score. Correct scoping (rl's wording): CEM was trained on LINES, "
           "and on lines both saturate at the cap -- so this says 'on an axis "
           "it was not trained for, learning did not beat the human-designed "
           "evaluation', NOT 'evolution failed'. Invisible in the lines column, "
           "which is exactly why the metric switch mattered.")
    else:
        fail("S4", "THE ADOPTED RESULT NO LONGER REPRODUCES: CEM-learned "
                   "weights now beat hand-tuned Dellacherie on score "
                   "(dellacherie %d/%d, median %+d). The comparison table and "
                   "the LEARN screen state the opposite."
             % (dw, len(SEEDS), dd))
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
        print("FINDINGS (an adopted result stopped reproducing, or the panel "
              "formula drifted):")
        for t, m in FAILED:
            print("  [%s] %s" % (t, m))
        return 1
    if not PASSED:
        print("RESULT: NOTHING WAS VERIFIED -- %d skipped, 0 passed. Do NOT "
              "read this as agreement." % len(SKIPPED))
        return 1
    print("RESULT: every adopted result still reproduces (%d checks)."
          % len(PASSED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
