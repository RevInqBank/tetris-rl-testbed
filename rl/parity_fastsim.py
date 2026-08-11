"""Prove `fastsim` is a faithful mirror of `engine`, or find where it is not.

WHY THIS FILE DECIDES WHETHER ANY TRAINING RESULT COUNTS
--------------------------------------------------------
Every number in `weights/` was produced by `fastsim.py`, not by the engine. If
the two simulators disagree -- a different drop rule, a different piece order, a
different line-clear shift -- then the trained policies are good at a game
nobody is playing, and the whole directory is worthless.

So this is the test that matters most in `rl/`. It replays IDENTICAL seeds and
IDENTICAL placement choices through both implementations and compares the board
after every single placement. Any divergence, at any step, fails.

THE ENGINE IS THE REFERENCE. If this test fails, `fastsim.py` is wrong.

WHAT IS COMPARED, STEP BY STEP
------------------------------
1. Piece sequence      -- the 7-bag / xorshift32 stream must match exactly.
2. Placement count     -- both must enumerate the same number of legal drops.
3. Placement geometry  -- the k-th placement must mean the same (rotation,
                          column) in both, which is what lets the two sides
                          make "the same choice".
4. Board after locking -- full 20x10 occupancy, every step.
5. lines_cleared       -- per placement.
6. landing_height      -- recomputed from the engine's `piece_cells` using the
                          normative definition in features.py, then compared to
                          fastsim's.
7. eroded_piece_cells  -- same.
8. Game over           -- both must die on the same placement.

WHY THE POLICY MATTERS AS MUCH AS THE COMPARISON
-------------------------------------------------
Random placement dies after ~23 pieces on a nearly empty board, so a random
parity test never visits a tall stack, a deep well, an overhang, or a spawn-area
collision -- exactly the states a trained policy lives in. This test therefore
defaults to driving BOTH simulators with the trained CEM weights, which keeps
games running for thousands of placements over crowded boards.

That is not hypothetical. The random-only version of this file passed cleanly
while the two harnesses still disagreed by 44% in reported lines, because the
disagreement only appeared on deep boards. `--policy random` is kept for a quick
smoke test, but `--policy cem` is the one that has teeth.

Run:  python3 parity_fastsim.py [--games 20] [--pieces 3000] [--policy cem]
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "engine"))

import engine                                    # noqa: E402
import fastsim as fs                             # noqa: E402
from features import BOARD_H, board_features     # noqa: E402


def engine_landing_and_eroded(info):
    """Recompute the two placement features from the engine's raw output.

    The engine deliberately does not know any feature definition -- it reports
    `piece_cells` as absolute (y, x) BEFORE clearing, and rl derives the rest.
    That keeps the normative definition in exactly one place (features.py).

    Engine rows include the 2-row spawn buffer, so board row r corresponds to
    engine y = r + BUFFER_ROWS, and height-from-floor is:

        height = 20 - r = 20 - (y - BUFFER_ROWS)
    """
    buf = engine.BUFFER_ROWS
    cells = info["piece_cells"]
    heights = [BOARD_H - (y - buf) for y, _x in cells]
    landing_height = sum(heights) / len(heights)

    lines = info["lines_cleared"]
    eroded = info.get("eroded_piece_cells")
    return landing_height, eroded, lines


def _load_policy():
    """The trained linear weights, or None if they are not on disk yet."""
    import json
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "weights", "cem_linear.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return np.array(json.load(f)["weights"])


def run_game(seed, max_pieces, rng, policy=None):
    """Replay one game through both simulators. Returns a list of failures.

    `policy` is either None (choose uniformly at random) or a weight vector, in
    which case BOTH sides are driven by the same greedy argmax over
    `fastsim`'s features -- the choice is made once and applied to both, so a
    divergence in the boards cannot be blamed on the two sides picking
    differently.
    """
    fails = []

    st = engine.new_game(seed)
    cols = [0] * 10
    # Engine convention: an empty column's "topmost filled row" is ROWS (22).
    top = [fs.TOTAL_ROWS] * 10
    bag = fs.BagRandomizer(seed)
    current = bag.next_piece()

    for step in range(max_pieces):
        # --- 1. piece identity ---
        if st.current != current:
            fails.append(f"seed {seed} step {step}: piece mismatch "
                         f"engine={engine.PIECE_NAMES[st.current]} "
                         f"fastsim={fs.PIECE_NAMES[current]}")
            break

        e_places = engine.legal_placements(st)
        f_places = fs.PLACEMENTS[current]

        # fastsim enumerates every column; ones that would stick out above the
        # board are rejected at simulate() time rather than at enumeration
        # time. Compare only the placements both consider legal.
        f_legal = []
        for i, rec in enumerate(f_places):
            res = fs.simulate(cols, top, rec)
            if res is not None:
                f_legal.append((i, rec, res))

        # --- 2. placement count ---
        if len(e_places) != len(f_legal):
            fails.append(f"seed {seed} step {step}: legal placement count "
                         f"engine={len(e_places)} fastsim={len(f_legal)} "
                         f"(piece {fs.PIECE_NAMES[current]})")
            break

        if not e_places:
            break                                  # both agree: dead

        # --- 3. geometry: k-th placement must be the same (rot, column) ---
        bad_geom = None
        for k, (ep, (_i, frec, _res)) in enumerate(zip(e_places, f_legal)):
            e_col = engine.placement_left_col(ep)
            f_rot, f_col = frec[0], frec[1]
            if e_col != f_col or ep[0] != f_rot:
                bad_geom = (k, (ep[0], e_col), (f_rot, f_col))
                break
        if bad_geom:
            k, e_rc, f_rc = bad_geom
            fails.append(f"seed {seed} step {step}: placement {k} geometry "
                         f"engine(rot,col)={e_rc} fastsim(rot,col)={f_rc} "
                         f"(piece {fs.PIECE_NAMES[current]})")
            break

        # --- make the SAME choice on both sides ---
        if policy is None:
            k = int(rng.integers(len(e_places)))
        else:
            best_v = -1e30
            k = 0
            for j, (_i, _frec, r) in enumerate(f_legal):
                ncols, _ntop, _lines, lh, er = r
                rt, ct, ho, cw, agg, bump = fs.board_features_bits(ncols)
                v = float(policy[0] * lh + policy[1] * er + policy[2] * rt
                          + policy[3] * ct + policy[4] * ho + policy[5] * cw
                          + policy[6] * agg + policy[7] * bump)
                if v > best_v + 1e-9:
                    best_v = v
                    k = j
        st, info = engine.apply_placement(st, e_places[k])
        _i, _frec, res = f_legal[k]
        cols, top, f_lines, f_lh, f_er = res

        # --- 5/6/7. per-placement info ---
        e_lh, e_er, e_lines = engine_landing_and_eroded(info)
        if e_lines != f_lines:
            fails.append(f"seed {seed} step {step}: lines_cleared "
                         f"engine={e_lines} fastsim={f_lines}")
            break
        if abs(e_lh - f_lh) > 1e-9:
            fails.append(f"seed {seed} step {step}: landing_height "
                         f"engine={e_lh} fastsim={f_lh}")
            break
        if e_er is not None and e_er != f_er:
            fails.append(f"seed {seed} step {step}: eroded_piece_cells "
                         f"engine={e_er} fastsim={f_er}")
            break

        # --- 4. board occupancy ---
        e_board = engine.board_array(st)
        f_board = fs.cols_to_array(cols)
        if not np.array_equal(e_board, f_board):
            diff = np.argwhere(e_board != f_board)
            fails.append(f"seed {seed} step {step}: BOARD MISMATCH at "
                         f"{len(diff)} cells, first (row,col)={tuple(diff[0])}\n"
                         f"engine:\n{e_board}\nfastsim:\n{f_board}")
            break

        # --- extra: the six board features must agree too ---
        if fs.board_features_bits(cols) != board_features(e_board):
            fails.append(f"seed {seed} step {step}: feature mismatch on "
                         f"identical boards (bug in fastsim's bit tricks)")
            break

        # --- 8. game over agreement ---
        # The engine ends on SPAWN COLLISION for the next piece, which is not
        # the same as "no legal placement remains". fastsim must agree exactly.
        current = bag.next_piece()
        f_over = fs.spawn_blocked(cols, current)
        if st.game_over != f_over:
            fails.append(f"seed {seed} step {step}: game over disagreement "
                         f"engine={st.game_over} fastsim={f_over} "
                         f"(next piece {fs.PIECE_NAMES[current]})")
            break
        if st.game_over:
            break

    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--pieces", type=int, default=3000)
    ap.add_argument("--first-seed", type=int, default=1)
    ap.add_argument("--policy", choices=["cem", "random"], default="cem",
                    help="cem drives both sides with the trained weights so "
                         "games get long and boards get tall; random dies in "
                         "~23 pieces and tests almost nothing")
    args = ap.parse_args()

    policy = _load_policy() if args.policy == "cem" else None
    if args.policy == "cem" and policy is None:
        print("[parity] cem_linear.json not found -- falling back to random")
    print(f"[parity] policy={args.policy if policy is not None else 'random'} | "
          f"{args.games} games x up to {args.pieces} placements | "
          f"engine vs fastsim\n")

    all_fails = []
    for g in range(args.games):
        seed = args.first_seed + g
        rng = np.random.default_rng(seed ^ 0x9A17)
        f = run_game(seed, args.pieces, rng, policy)
        all_fails.extend(f)
        if f:
            print(f"  seed {seed}: FAIL")
            for line in f[:2]:
                print("    " + line.replace("\n", "\n    "))
            if len(all_fails) > 6:
                break
    if not all_fails:
        print(f"  all {args.games} games identical at every placement")
        print("\nPASS -- fastsim is a faithful mirror of engine")
        return 0
    print(f"\nFAILED -- {len(all_fails)} divergences. The ENGINE is the "
          f"reference; fix fastsim.py.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
