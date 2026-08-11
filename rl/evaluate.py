"""Evaluate every strategy on one identical seed set, ON THE ENGINE.

WHY THIS RUNS ON `engine`, NOT ON `fastsim`
-------------------------------------------
Training rollouts use `fastsim` because they need the throughput. Evaluation
does not: ten games is nothing. So by the lead's ruling,

    EVERY NUMBER THAT REACHES A HUMAN COMES OUT OF `engine`.

That is not ceremony. `fastsim` is a second implementation of the rules, and a
second implementation can drift. `parity_fastsim.py` is the first line of
defence, but if it ever missed something, scoring on the engine means the drift
shows up here as a performance drop instead of silently inflating a number in
the README. A policy that was trained against a subtly wrong simulator will
simply play worse on the real one, and this table is where that becomes visible.

ONE SEED SET FOR EVERYONE
-------------------------
All eight panels play the SAME games. Comparing strategies across different
seeds would let piece-sequence luck masquerade as skill.

    python3 evaluate.py                          # holdout seeds, cap 5000
    python3 evaluate.py --cap 50000
    python3 evaluate.py --only cem_linear search_1ply

Writes `weights/eval_summary.json` and prints a table.

READING THE TABLE
-----------------
`hit cap` is the column that matters most. A strategy that reached the piece cap
did not finish its game -- its line count is a FLOOR set by the cap, not a
measurement. Two strategies that both hit the cap are indistinguishable here no
matter how different their totals look, and the summary says "saturated" rather
than inventing a ranking.

SCORE COLUMNS
-------------
Lines saturate: the good policies never die, so lines stop discriminating. The
score metrics do not saturate, because a tetris is worth far more than four
singles:
    score_per_piece  -- the main axis; independent of game length
    tetris_rate      -- fraction of cleared lines that came from 4-line clears
Both come from the engine's own scoring (including back-to-back and combo), so
they are the same numbers the UI shows.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "engine"))
import engine as E                                       # noqa: E402

import features as F                                     # noqa: E402
from artifacts import ROSTER, dump_json                   # noqa: E402
from features import SCORE_EPS, argmax_stable             # noqa: E402
from nn import MLP                                        # noqa: E402
from pg import FEATURE_SCALE                              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_DIR = os.path.join(HERE, "..", "weights")

DEATH_PENALTY = -1e6      # matches search.py: a forced loss one ply ahead


def load_spec(name, filename):
    if filename is None:
        return {"name": name, "kind": name}
    path = os.path.join(WEIGHTS_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        d = json.load(f)
    # READ BY NAME, NOT BY EQUALITY. Requiring `features` to equal the frozen
    # 8-tuple rejected every file trained on a larger set -- which is the whole
    # point of having named sets. What must be checked is that every name is
    # one we can actually compute; an unknown name is a hard error rather than
    # a zero-filled column.
    unknown = [n for n in d["features"] if n not in F.KNOWN_FEATURES]
    if unknown:
        raise ValueError(f"{filename}: unknown feature(s) {unknown}; "
                         f"known: {sorted(F.KNOWN_FEATURES)}")
    return d


def _build_net(spec):
    layers = spec["layers"]
    sizes = [len(layers[0]["W"])] + [len(l["W"][0]) for l in layers]
    net = MLP(sizes, np.random.default_rng(0))
    for i, l in enumerate(layers):
        net.W[i][...] = np.array(l["W"])
        net.b[i][...] = np.array(l["b"])
    return net


def afterstate_features(next_state, info, names):
    """One candidate placement's feature vector, in the order `names` gives.

    The two placement features come from raw geometry via `features.py`, which
    owns the formulas; the rest are computed on the visible field of the
    resulting board. Ordering by the file's own `features` list is what lets
    8-feature and 10-feature policies run through the same evaluator.
    """
    er = info.get("eroded_piece_cells")
    if er is None:
        er = info["lines_cleared"] * info["cleared_piece_cells"]
    return F.extract_named(names, E.board_array(next_state),
                           F.landing_height_from_cells(info["piece_cells"]), er)


def play(job):
    """One game of one strategy, played entirely on the engine."""
    spec, seed, cap, difficulty = job
    kind = spec.get("kind")
    name = spec.get("name")

    rng = np.random.default_rng(seed ^ 0xE7A1)
    net = _build_net(spec) if kind in ("mlp", "policy_mlp") else None
    names = tuple(spec.get("features") or F.FEATURE_NAMES)
    if kind == "linear":
        w = np.array(spec["weights"])
    elif name == "dellacherie":
        w = F.DELLACHERIE_WEIGHTS
        names = F.FEATURE_NAMES
    else:
        w = None
    scaled = kind in ("mlp", "policy_mlp")
    # Use the FILE's scale vector, not a module constant. The constant is the
    # 8-feature one, so a wells10 net would be normalised by the wrong divisors
    # (and by the wrong LENGTH) with no error raised.
    fscale = np.array(spec["feature_scale"]) if spec.get("feature_scale") \
        else FEATURE_SCALE
    # Dispatch on what the FILE declares, not on its name. Matching by name
    # meant `search_1ply_sum` silently fell through to plain greedy and
    # reported CEM's numbers as if they were the summed search rule's.
    search_cfg = (spec.get("meta") or {}).get("search")
    use_search = search_cfg is not None
    search_rule = (search_cfg or {}).get("rule", "leaf")

    # difficulty must be threaded through: E.new_game defaults to normal, so
    # omitting it produced normal-mode numbers under a "hard"/"extreme" label
    # with no error anywhere. checker caught this.
    s = E.new_game(seed, difficulty=difficulty)
    lines_total = 0
    score_total = 0
    score_norm = 0.0
    pieces = 0
    tetris_lines = 0
    clears_by_n = [0, 0, 0, 0, 0]
    t0 = time.perf_counter()

    while pieces < cap and not s.game_over:
        places = E.legal_placements(s)
        if not places:
            break

        # Expand every candidate once. apply_placement does not mutate `s`.
        cand = [E.apply_placement(s, p) for p in places]

        if name == "random":
            idx = int(rng.integers(len(cand)))
        else:
            feats = np.array([afterstate_features(ns, inf, names)
                              for ns, inf in cand], dtype=np.float64)
            # A lookahead policy must be BLIND when the mode hides the next
            # piece. Threading `difficulty` into new_game is not enough: this
            # evaluator expands afterstates, and an afterstate carries
            # `.current` for the following piece, so the search would keep
            # seeing the future in extreme mode and the whole point of the mode
            # (watching the lookahead advantage evaporate) would be lost.
            # Degrade explicitly to 0-ply instead, which is what the engine's
            # NextPeekBlocked contract asks callers to do.
            if use_search and E.next_visible_count(s) == 0:
                scores = feats @ w                     # 0-ply == plain greedy
            elif use_search:
                # 2-ply lookahead with the known next piece.
                #   rule "leaf" (correct)  : score(p1) = max_p2 v(p1 -> p2)
                #   rule "sum"  (legacy)   : score(p1) = v(p1) + max_p2 v(p1 -> p2)
                # A p1 that leaves no legal reply is a forced loss next ply --
                # something the greedy policy is structurally blind to.
                base = feats @ w
                total = np.empty(len(cand))
                for i, (ns, _inf) in enumerate(cand):
                    replies = E.legal_placements(ns)
                    if not replies:
                        total[i] = DEATH_PENALTY
                        continue
                    best2 = -1e30
                    for p2 in replies:
                        ns2, inf2 = E.apply_placement(ns, p2)
                        v2 = float(afterstate_features(ns2, inf2, names) @ w)
                        if v2 > best2 + SCORE_EPS:
                            best2 = v2
                    total[i] = ((base[i] + best2) if search_rule == "sum"
                                else best2)
                scores = total
            elif scaled:
                scores = net(feats / fscale)
            else:
                scores = feats @ w
            # Ties go to the lowest (rot, x). MUST be argmax_stable, not
            # np.argmax: scores collide at the last bit and plain argmax
            # resolves them differently depending on the dot-product path.
            idx = argmax_stable(scores)

        level_before = s.level        # the level the engine scored this clear at
        s, info = cand[idx]
        n = info["lines_cleared"]
        lines_total += n
        score_total += info["score_delta"]
        # Level-normalised: strips out the 1 + lines/10 multiplier so a tetris
        # is worth the same early and late. This is what cem_score optimises,
        # and without it the column just re-measures survival.
        score_norm += info["score_delta"] / max(1, level_before)
        clears_by_n[n] += 1
        if n == 4:
            tetris_lines += 4
        pieces += 1

    return {
        "seed": seed,
        "difficulty": difficulty,
        "lines": lines_total,
        "score": score_total,
        "pieces": pieces,
        "died": bool(s.game_over) or pieces < cap,
        "hit_cap": pieces >= cap,
        "score_per_piece": score_total / pieces if pieces else 0.0,
        "norm_score_per_piece": score_norm / pieces if pieces else 0.0,
        "tetris_rate": tetris_lines / lines_total if lines_total else 0.0,
        "clears_by_n": clears_by_n,
        "seconds": round(time.perf_counter() - t0, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*",
                    default=list(range(900_001, 900_011)))
    ap.add_argument("--cap", type=int, default=5000)
    ap.add_argument("--difficulty", choices=["normal", "hard", "extreme"],
                    default="normal",
                    help="normal = next 5 + hold, hard = next 1, extreme = no "
                         "preview. NOTE: this evaluator's search still expands "
                         "afterstates, which reveals the next piece regardless; "
                         "hard/extreme numbers from here are NOT trustworthy for "
                         "lookahead policies until that is handled.")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=64)
    ap.add_argument("--out", default=os.path.join(WEIGHTS_DIR, "eval_summary.json"))
    args = ap.parse_args()

    difficulty = {"normal": E.DIFFICULTY_NORMAL,
                  "hard": E.DIFFICULTY_HARD,
                  "extreme": E.DIFFICULTY_EXTREME}[args.difficulty]
    if args.difficulty != "normal":
        print(f"[eval] WARNING: difficulty={args.difficulty}. Lookahead "
              f"policies (meta.search) still see the next piece through "
              f"afterstate expansion, so their numbers here do not reflect the "
              f"information restriction. Use web/policies.js for those.")

    jobs, meta, missing = [], [], []
    for sid, panel, fname, label, _kind, critic, _plan in ROSTER:
        if args.only and sid not in args.only:
            continue
        spec = load_spec(sid, fname)
        if spec is None:
            missing.append(sid)
            continue
        spec.setdefault("name", sid)
        meta.append((sid, panel, label, critic, spec))
        for sd in args.seeds:
            jobs.append((spec, sd, args.cap, difficulty))

    if missing:
        print(f"[eval] SKIPPED (no weights file): {', '.join(missing)}")
    print(f"[eval] engine-based | {len(meta)} strategies x {len(args.seeds)} "
          f"seeds | cap {args.cap} pieces\n")

    with mp.Pool(args.workers) as pool:
        flat = pool.map(play, jobs)

    n_seeds = len(args.seeds)
    summary, rows = {}, []
    for i, (sid, panel, label, critic, _spec) in enumerate(meta):
        games = flat[i * n_seeds:(i + 1) * n_seeds]
        lines = [g["lines"] for g in games]
        caps = sum(g["hit_cap"] for g in games)
        rec = {
            "panel": panel, "label": label, "critic_role": critic,
            # MEDIAN IS THE PRIMARY STATISTIC. These distributions are
            # heavy-tailed -- one lucky game can supply half the total, so a
            # mean silently reports that game's luck as the policy's skill.
            # Measured: dqn mean_lines 103.7 with 53% of it from one game of
            # 547; its median is 40. Ranking by mean puts DQN above A2C,
            # ranking by median reverses it. Both are kept, and the gap
            # between them is itself a result.
            "median_lines": float(np.median(lines)),
            "mean_lines": float(np.mean(lines)),
            "min_lines": int(np.min(lines)), "max_lines": int(np.max(lines)),
            "median_score": float(np.median([g["score"] for g in games])),
            "median_norm_score_per_piece": float(np.median(
                [g["norm_score_per_piece"] for g in games])),
            "median_tetris_rate": float(np.median([g["tetris_rate"] for g in games])),
            "mean_over_median_lines": (float(np.mean(lines) / np.median(lines))
                                       if np.median(lines) > 0 else None),
            "n": n_seeds,
            "hit_cap_count": caps,
            "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seeds": list(args.seeds),
            "piece_cap": args.cap,
            "difficulty": args.difficulty,
            "mean_score": float(np.mean([g["score"] for g in games])),
            "mean_score_per_piece": float(np.mean([g["score_per_piece"] for g in games])),
            "mean_norm_score_per_piece": float(np.mean([g["norm_score_per_piece"] for g in games])),
            "mean_tetris_rate": float(np.mean([g["tetris_rate"] for g in games])),
            "mean_pieces": float(np.mean([g["pieces"] for g in games])),
            "games_hit_cap": caps, "n_games": n_seeds,
            "saturated": caps == n_seeds,
            "evaluated_on": "engine",
            "per_game": games,
        }
        summary[sid] = rec
        rows.append((panel, label, rec))

    rows.sort()
    w1 = max(len(r[1]) for r in rows) + 2
    print(f"{'#':>2}  {'strategy':<{w1}}{'med lines':>11}{'score/pc':>10}"
          f"{'norm s/pc':>11}{'tetris%':>9}{'pieces':>9}{'hit cap':>9}")
    print("-" * (4 + w1 + 11 + 10 + 11 + 9 + 9 + 9))
    for panel, label, rec in rows:
        print(f"{panel:>2}  {label:<{w1}}{rec['median_lines']:>11.1f}"
              f"{rec['mean_score_per_piece']:>10.0f}"
              f"{rec['mean_norm_score_per_piece']:>11.1f}"
              f"{rec['mean_tetris_rate'] * 100:>8.1f}%"
              f"{rec['mean_pieces']:>9.0f}"
              f"{rec['games_hit_cap']:>6}/{rec['n_games']}")
    print("\n  score/pc  = raw engine score per piece. Climbs with the level "
          "multiplier, so it\n              partly re-measures survival -- read "
          "it with `hit cap` in mind.\n"
          "  norm s/pc = score/level per piece. Level multiplier removed, so a "
          "tetris is worth\n              the same early and late. This is what "
          "panel 9 was trained on.")

    sat = [r[1] for r in rows if r[2]["saturated"]]
    if sat:
        print(f"\n[eval] SATURATED at the {args.cap}-piece cap -- these never "
              f"died, so their line counts are floors, not measurements:")
        for x in sat:
            print(f"         - {x}")
        print("       They are NOT ranked against each other by the lines column.\n"
              "       Use score/piece and tetris%, which do not saturate.")

    # ACCUMULATE, never clobber. Evaluating one strategy with --only used to
    # rewrite the whole file with a single entry, and nothing complained: the
    # comparison table just rendered one row, which reads as "the others have
    # not been evaluated yet" rather than "eight results were destroyed".
    # Each entry carries its own evaluated_at / seeds / piece_cap so mixed
    # generations stay distinguishable.
    merged = {}
    if os.path.exists(args.out):
        try:
            with open(args.out) as f:
                prev = json.load(f)
            merged = prev.get("strategies", {}) or {}
            kept = [k for k in merged if k not in summary]
            if kept:
                print(f"[eval] preserved {len(kept)} earlier result(s): "
                      f"{', '.join(sorted(kept))}")
        except (ValueError, OSError) as e:
            print(f"[eval] WARNING: could not read existing {args.out} ({e}); "
                  f"writing fresh")
    # Guard against SILENT DOWNGRADE. Accumulation merges by strategy id, so a
    # later small-n run replaces an earlier large-n one and the table quietly
    # loses statistical power -- the row still looks fine, it just stops being
    # able to support a ranking. Hit this immediately: a full cap-3000 sweep at
    # n=10 wiped four n=200 rows.
    for sid, rec in summary.items():
        old = merged.get(sid)
        if old and old.get("n", 0) > rec.get("n", 0):
            print(f"[eval] WARNING: {sid} n {old['n']} -> {rec['n']} "
                  f"(replacing a LARGER sample with a smaller one). "
                  f"Re-run that strategy at n={old['n']} to restore it.")
    merged.update(summary)

    # Accumulating across runs makes it possible to compare rows that were
    # measured under different budgets. A strategy that hit a 20,000-piece cap
    # and one that hit a 3,000-piece cap are not comparable on lines at all, and
    # nothing in the rendered table would say so.
    caps = {}
    for sid, rec in merged.items():
        caps.setdefault(rec.get("piece_cap"), []).append(sid)
    if len(caps) > 1:
        print("\n[eval] WARNING: rows were measured under DIFFERENT piece caps. "
              "Lines are not comparable across them.")
        for cap, sids in sorted(caps.items(), key=lambda kv: (kv[0] is None, kv[0])):
            print(f"         cap {cap}: {', '.join(sorted(sids))}")
        print("       Re-run without --only to put every strategy on one budget.")

    known = {sid for sid, *_ in ROSTER}
    missing_now = sorted(known - set(merged))
    if missing_now:
        print(f"[eval] NOTE: {len(merged)}/{len(known)} strategies in the file. "
              f"Never evaluated: {', '.join(missing_now)}")

    dump_json(args.out, {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evaluated_on": "engine",
        "difficulty": args.difficulty,
        "primary_statistic": "median",
        "statistic_note": "Rank by median. These distributions are heavy-tailed; "
                          "a mean can be dominated by one lucky game. "
                          "mean_over_median_lines >= 1.5 means exactly that.",
        "tetris_rate_units": "fraction in [0,1], NOT percent",
        "last_run": {"seeds": args.seeds, "piece_cap": args.cap},
        "seeds_note": "Held-out: disjoint from the 1..100000 training range.",
        "cap_note": ("hit_cap_count 는 '안 죽음'이 아니라 "
                     f"'{args.cap:,}조각까지 안 죽음'이다. 무제한 평가는 원리적으로 "
                     "끝나지 않는다 — cem_linear 는 20만 조각에서도 안 죽는다. "
                     "정직한 형태는 '무제한'이 아니라 '충분히 큰 상한 + 상한 명시'다."),
        "strategy_count": len(merged),
        "roster_count": len(known),
        "strategies": merged,
    })
    print(f"\n[eval] wrote {args.out} ({len(merged)} strategies)")


if __name__ == "__main__":
    main()
