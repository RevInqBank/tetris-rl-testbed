"""1-ply lookahead on top of a learned value function.

WHERE THIS SITS ON THE STUDY-PLAN AXIS
--------------------------------------
This is panel 8, the AlphaZero section of week 4 -- reduced to its skeleton.

The study plan draws the distinction sharply: in the first six algorithms the
critic either scores the update (baseline / bootstrap target) or IS the policy
(DQN's argmax). In AlphaZero the critic does neither. Search performs the policy
improvement, and the value network only evaluates leaves. The improvement
operator lives OUTSIDE the network.

That is exactly what happens here. The weight vector is not retrained -- it is
`cem_linear.json` verbatim. The only thing added is that the agent expands the
current piece AND the next piece before scoring, and evaluates the resulting
leaf with the same learned value. Any improvement over panel 3 is attributable
to search alone, because the value function is byte-identical.

This is the cheapest honest demonstration of the AlphaZero idea that fits in
this project: no MCTS, no policy head, no self-play, and a lookahead of one
extra ply. What survives is the structural claim -- a fixed evaluator plus
search beats the same evaluator used greedily.

THE RULE -- CORRECTED 2026-08-07
--------------------------------
    score(p1) = max over legal p2 of  w . f(afterstate after p1 then p2)

LEAF EVALUATION ONLY. The value of a move is the value of the best position it
can lead to, full stop.

The original rule here ADDED the two plies:

    score(p1) = w . f(after p1) + max_p2 w . f(after p1 then p2)      # WRONG

That is not a lookahead, it is a different evaluation function. `w` was fitted
by CEM to score a position ONE greedy step ahead; summing the values of two
states at different depths double-counts the intermediate node and effectively
weights the first ply twice. It also adds `landing_height` and
`eroded_piece_cells` across two different placements, which has no meaning --
each is a property of the move that produced it.

This was not a theoretical worry. checker measured the summed rule LOSING to
plain greedy CEM on all three seeds (780,850 / 803,800 / 779,350 against
799,700 / 822,550 / 811,200), and the "degraded" extreme-difficulty run -- where
no next piece is visible and the search collapses to greedy -- scored HIGHER
than the normal-difficulty search. Turning the search off was an improvement,
which is the signature of a broken evaluation, not of a search that does not
help.

The old rule is preserved as `--rule sum` and shipped as
`weights/search_1ply_sum.json` so the comparison stays on the record.

The next piece is KNOWN, not sampled: 7-bag Tetris shows the queue, so taking a
max over p2 is correct rather than optimistic. If p1 leaves no legal p2 the
position is lost on the following piece, and it gets DEATH_PENALTY so the search
avoids walking into a forced loss one ply early -- something the greedy policy
is structurally blind to.

COST
----
Roughly 34 x 34 ~= 1,150 afterstate evaluations per move against 34 for greedy,
so about 30x slower: a few hundred moves per second. Fine for evaluation and for
the browser, which animates one move at a time anyway.
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np

from fastsim import (PLACEMENTS, TOTAL_ROWS, BagRandomizer,
                     board_features_bits, simulate, spawn_blocked)
from features import SCORE_EPS

# Losing the game is worth far less than any feature combination can express.
# It has to be finite (so that "least bad" is still ordered) but far outside the
# range of ordinary scores, which sit within roughly +/- 200 for unit weights.
DEATH_PENALTY = -1e6

RULES = ("leaf", "sum")


def value(w, cols, landing_height, eroded):
    """w . f for one afterstate."""
    rt, ct, ho, cw, agg, bump = board_features_bits(cols)
    return (w[0] * landing_height + w[1] * eroded + w[2] * rt + w[3] * ct
            + w[4] * ho + w[5] * cw + w[6] * agg + w[7] * bump)


def best_placement_1ply(w, cols, top, current: int, nxt: int, rule: str = "leaf"):
    """Pick a placement for `current`, looking one piece ahead at `nxt`.

    rule="leaf" (correct): score(p1) = max_p2 v(after p1 then p2)
    rule="sum"  (legacy):  score(p1) = v(after p1) + max_p2 v(after p1 then p2)

    Returns (best_record, best_result), or (None, None) if every placement loses.
    """
    w0, w1, w2, w3, w4, w5, w6, w7 = (float(x) for x in w)
    best_total = -1e30
    best_rec = None
    best_res = None

    for rec in PLACEMENTS[current]:
        res = simulate(cols, top, rec)
        if res is None:
            continue
        ncols, ntop, _lines, lh, er = res
        rt, ct, ho, cw, agg, bump = board_features_bits(ncols)
        v1 = (w0 * lh + w1 * er + w2 * rt + w3 * ct
              + w4 * ho + w5 * cw + w6 * agg + w7 * bump)

        # Best reply with the known next piece.
        v2_best = -1e30
        if spawn_blocked(ncols, nxt):
            # p1 makes the NEXT piece unspawnable -- an immediate loss that the
            # greedy policy cannot see. Treat it as the forced-loss case.
            v2_best = -1e30
        else:
            for rec2 in PLACEMENTS[nxt]:
                res2 = simulate(ncols, ntop, rec2)
                if res2 is None:
                    continue
                n2cols, n2top, _l2, lh2, er2 = res2
                rt2, ct2, ho2, cw2, agg2, bump2 = board_features_bits(n2cols)
                v2 = (w0 * lh2 + w1 * er2 + w2 * rt2 + w3 * ct2
                      + w4 * ho2 + w5 * cw2 + w6 * agg2 + w7 * bump2)
                if v2 > v2_best + SCORE_EPS:
                    v2_best = v2
        if v2_best <= -1e29:
            v2_best = DEATH_PENALTY      # p1 leaves a forced loss next ply

        # Leaf evaluation: the position two plies out is the whole score.
        # The legacy "sum" rule is kept only so the two can be compared.
        total = (v1 + v2_best) if rule == "sum" else v2_best
        if total > best_total + SCORE_EPS:
            best_total = total
            best_rec = rec
            best_res = res

    return best_rec, best_res


def rollout_1ply(w, seed: int, max_pieces: int = 500, rule: str = "leaf"):
    """Play one full game with 1-ply search. Same return shape as cem.rollout."""
    cols = [0] * 10
    top = [TOTAL_ROWS] * 10
    bag = BagRandomizer(seed)
    current = bag.next_piece()
    nxt = bag.next_piece()

    lines_total = 0
    pieces = 0
    while pieces < max_pieces:
        if spawn_blocked(cols, current):
            return lines_total, pieces, True
        _rec, res = best_placement_1ply(w, cols, top, current, nxt, rule)
        if res is None:
            return lines_total, pieces, True
        cols, top, lines, _lh, _er = res
        lines_total += lines
        pieces += 1
        current = nxt
        nxt = bag.next_piece()
    return lines_total, pieces, False


def load_linear_weights(path):
    with open(path) as f:
        d = json.load(f)
    from features import FEATURE_NAMES
    if tuple(d["features"]) != tuple(FEATURE_NAMES):
        raise ValueError(
            f"feature order mismatch in {path}:\n  file: {d['features']}\n"
            f"  code: {list(FEATURE_NAMES)}")
    if d["kind"] != "linear":
        raise ValueError(f"expected kind=linear, got {d['kind']}")
    return np.array(d["weights"], dtype=np.float64)


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--weights", default=os.path.join(here, "..", "weights",
                                                      "cem_linear.json"))
    ap.add_argument("--seeds", type=int, nargs="*", default=list(range(900_001, 900_011)))
    ap.add_argument("--cap", type=int, default=20_000)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--rule", choices=RULES, default="leaf",
                    help="leaf = score(p1) is the best 2-ply leaf value "
                         "(correct); sum = legacy, adds the p1 value too")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import multiprocessing as mp
    from datetime import datetime, timezone
    from features import FEATURE_NAMES

    if args.out is None:
        args.out = os.path.join(here, "..", "weights",
                                "search_1ply.json" if args.rule == "leaf"
                                else "search_1ply_sum.json")
    w = load_linear_weights(args.weights)
    print(f"[search] loaded value function from {args.weights}")
    print(f"[search] 1-ply over {len(args.seeds)} held-out seeds, cap {args.cap}")

    with mp.Pool(args.workers) as pool:
        results = pool.starmap(rollout_1ply,
                               [(w, s, args.cap, args.rule) for s in args.seeds])
    lines = [r[0] for r in results]
    ev = {
        "seeds": list(args.seeds),
        "lines": lines,
        "pieces": [r[1] for r in results],
        "mean_lines": float(np.mean(lines)),
        "min_lines": int(np.min(lines)),
        "max_lines": int(np.max(lines)),
    }
    for s, r in zip(args.seeds, results):
        print(f"   seed {s}: {r[0]:7d} lines, {r[1]:7d} pieces"
              f"{'  (died)' if r[2] else '  (hit cap)'}")
    print(f"[search] mean lines {ev['mean_lines']:.1f}")

    payload = {
        "name": "search_1ply" if args.rule == "leaf" else "search_1ply_sum",
        "kind": "linear",
        "features": list(FEATURE_NAMES),
        "weights": [float(x) for x in w],
        "meta": {
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "algorithm": f"1-ply lookahead (current + next piece) over a "
                         f"CEM-learned linear value, rule={args.rule}",
            "note": "Weights are copied verbatim from cem_linear.json; no "
                    "training was done, so any difference from panel 3 is "
                    "attributable to search alone. The web UI must run this with "
                    "2-ply search; run greedily it reproduces panel 3 exactly.",
            "search": {"plies": 2, "uses_next_piece": True,
                       "rule": args.rule,
                       "formula": ("score(p1) = max_p2 v(after p1 -> p2)"
                                   if args.rule == "leaf"
                                   else "score(p1) = v(after p1) + max_p2 v(after p1 -> p2)"),
                       "death_penalty": DEATH_PENALTY,
                       "rule_change_note":
                           "2026-08-07: the default changed from 'sum' to 'leaf'. "
                           "The summed rule adds values of states at two "
                           "different depths, double-counting the intermediate "
                           "node, and adds landing_height/eroded_piece_cells "
                           "across two different placements, which is "
                           "meaningless. Measured: the summed rule LOST to plain "
                           "greedy CEM on all three test seeds, and collapsing "
                           "the search (extreme difficulty, no next piece) "
                           "IMPROVED the score. 'sum' is preserved as "
                           "search_1ply_sum.json for the record."},
            "source_weights": os.path.basename(args.weights),
            # Always present, false until verified. `null` reads as "unknown
            # provenance"; artifacts.py --parity run raises this to true.
            "parity_verified": False,
            "eval": ev,
            "eval_piece_cap": args.cap,
        },
    }
    from artifacts import dump_json
    dump_json(args.out, payload)
    print(f"[search] wrote {args.out}")


if __name__ == "__main__":
    main()
