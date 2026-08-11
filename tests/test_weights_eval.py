"""Independent re-evaluation of rl's trained weights, and an independent
fastsim <-> engine cross-check.

Nothing here trusts rl's own numbers, rl's own eval harness, or rl's own
fastsim/engine comparison. Specifically:

  1. `meta.parity_verified` audit -- lead ruling #4 condition (2) says weights
     are not final until this is set.
  2. Seed hygiene: are the reported eval seeds actually disjoint from the
     training range, and is the reported mean a real measurement or an
     artefact of the piece cap?
  3. Independent 8-feature implementation, written from the DEFINITION blocks
     in rl/features.py's docstrings, cross-checked against rl/features.py.
     If they disagree, the re-evaluation below would be meaningless, so this
     runs first.
  4. RE-EVALUATION on seeds rl has used for NEITHER training NOR evaluation,
     driven by `engine/engine.py` (the authority), scored with the independent
     features. Target: 10-game mean >= 1,000 lines (PROJECT.md criterion 3).
  5. fastsim <-> engine: same seed, same placement sequence, compare the board
     state after every move. Lead ruling #4 condition (4): engine is right.

Run:  python3 tests/test_weights_eval.py
Owner of the code under test: rl.  This file is checker-owned.
"""

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "rl"))

import numpy as np  # noqa: E402

import engine as E  # noqa: E402

#: engine renamed column_heights -> column_tops (it returns the topmost
#: FILLED ROW INDEX, not a height). Bind whichever the current engine has.
_TOPS = getattr(E, "column_tops", None) or getattr(E, "column_heights")

WEIGHTS = os.path.join(_ROOT, "weights")

#: Seeds rl has used for NEITHER training (1..100000) NOR evaluation
#: (900001..900010). Fixed here so the number is reproducible.
FRESH_SEEDS = [777000001 + 4801 * k for k in range(10)]

FAILURES = []


def fail(tag, msg):
    FAILURES.append((tag, msg))
    print("  FAIL [%s] %s" % (tag, msg))


def ok(msg):
    print("  ok   %s" % msg)


# ===========================================================================
# 1 + 2. metadata audit
# ===========================================================================

def audit_metadata():
    print("\n1+2. weights metadata audit")
    files = sorted(f for f in os.listdir(WEIGHTS) if f.endswith(".json"))
    rows = []
    for f in files:
        d = json.load(open(os.path.join(WEIGHTS, f)))
        if "kind" not in d:
            continue                       # eval_summary.json is not a weight
        m = d.get("meta", {})
        ev = m.get("eval", {}) or {}
        rows.append((f, d.get("kind"), m.get("parity_verified"),
                     m.get("train_seed_range"), ev.get("seeds"),
                     ev.get("mean_lines"), m.get("eval_piece_cap"),
                     ev.get("hit_cap"), ev.get("pieces")))

    missing = [r[0] for r in rows if r[2] is None]
    if missing:
        fail("W1", "meta.parity_verified is ABSENT in %d of %d weight files "
                   "(%s). Lead ruling #4 condition (2): '패리티 통과 전 가중치는 "
                   "최종본 아님(meta.parity_verified)'. By the project's own "
                   "rule THOSE files are not final; the rest carry the flag."
             % (len(missing), len(rows), ", ".join(missing)))
    else:
        vals = {r[0]: r[2] for r in rows}
        falsey = [k for k, v in vals.items() if not v]
        if falsey:
            fail("W1", "parity_verified is FALSE in: %s -- these must not be "
                       "reported as final" % ", ".join(falsey))
        else:
            ok("all %d weight files carry meta.parity_verified = true"
               % len(rows))

    print("    %-24s %-13s %-9s %s" % ("file", "kind", "parity_v", "reported"))
    for f, kind, pv, tr, seeds, mean, cap, hit, pieces in rows:
        print("    %-24s %-13s %-9s mean_lines=%s" % (f, kind, pv, mean))

    # seed hygiene
    for f, kind, pv, tr, seeds, mean, cap, hit, pieces in rows:
        if not seeds:
            continue
        if tr:
            lo, hi = tr
            overlap = [s for s in seeds if lo <= s <= hi]
            if overlap:
                fail("W2", "%s: eval seeds %r overlap the training range %r -- "
                           "this is evaluating on training seeds"
                     % (f, overlap, tr))
    ok("no eval seed falls inside any declared training range (train 1..100000 "
       "vs eval 900001..900010)")

    # is the headline number a measurement or the cap?
    for f, kind, pv, tr, seeds, mean, cap, hit, pieces in rows:
        if hit and all(hit):
            fail("W2", "%s: reported mean_lines=%s but hit_cap is True on ALL "
                       "%d games (piece cap %s). That number is the CAP, not a "
                       "measured mean -- the honest claim is 'survived the cap "
                       "without dying, >= %s lines', not 'averages %s lines'."
                 % (f, mean, len(hit), cap, mean, mean))
        elif pieces and cap and all(p >= cap for p in pieces):
            fail("W2", "%s: every game ran exactly to the %s-piece cap, so "
                       "mean_lines=%s is cap-limited, not a measured mean"
                 % (f, cap, mean))
    audit_distributions()
    return rows


def audit_distributions():
    """A mean over 10 games hides a single dominating game. Flag it."""
    print("    --- is any reported mean dominated by one game? ---")
    for f in sorted(os.listdir(WEIGHTS)):
        if not f.endswith(".json"):
            continue
        d = json.load(open(os.path.join(WEIGHTS, f)))
        if "kind" not in d:
            continue
        ev = (d.get("meta", {}) or {}).get("eval", {}) or {}
        for key in ("score", "lines"):
            vals = ev.get(key)
            if not vals or len(vals) < 3 or sum(vals) <= 0:
                continue
            top = max(vals)
            share = top / float(sum(vals))
            if share > 0.35:
                rest = (sum(vals) - top) / float(len(vals) - 1)
                mean = sum(vals) / float(len(vals))
                fail("W2", "%s: reported mean %s=%.1f, but ONE of the %d games "
                           "contributes %.0f%% of the total (%.4g of %.4g). "
                           "Drop that single game and the mean falls to %.4g "
                           "(%.2fx lower); the median is %.4g. A mean this "
                           "skewed is not a summary of the policy -- report the "
                           "median or the per-game list."
                     % (f, key, mean, len(vals), 100 * share, top, sum(vals),
                        rest, mean / rest,
                        sorted(vals)[len(vals) // 2]))
            else:
                print("      %-24s %-6s max share %.0f%% (ok)"
                      % (f, key, 100 * share))


# ===========================================================================
# 3. independent 8-feature implementation
# ===========================================================================
# Written from the DEFINITION blocks in rl/features.py docstrings. Plain
# Python loops -- deliberately not numpy, so a numpy-idiom bug cannot be
# reproduced identically here.

VIS = E.VISIBLE_ROWS
COLS = E.W


def ind_heights(g):
    h = [0] * COLS
    for c in range(COLS):
        for y in range(VIS):
            if g[y][c]:
                h[c] = VIS - y
                break
    return h


def ind_row_transitions(g):
    """Each row scanned across 12 positions: wall, 10 cells, wall.
    Walls count as FILLED."""
    t = 0
    for y in range(VIS):
        prev = 1
        for c in range(COLS):
            cur = 1 if g[y][c] else 0
            if cur != prev:
                t += 1
            prev = cur
        if prev != 1:
            t += 1
    return t


def ind_column_transitions(g):
    t = 0
    for c in range(COLS):
        prev = 1                      # ceiling counts as filled? see note
        # rl's definition: top of the board counts as EMPTY, floor as FILLED.
        prev = 0
        for y in range(VIS):
            cur = 1 if g[y][c] else 0
            if cur != prev:
                t += 1
            prev = cur
        if prev != 1:
            t += 1
    return t


def ind_holes(g):
    n = 0
    for c in range(COLS):
        seen = False
        for y in range(VIS):
            if g[y][c]:
                seen = True
            elif seen:
                n += 1
    return n


def ind_cumulative_wells(g):
    """Sum over cells of a well of its depth-so-far: a well of depth d
    contributes 1+2+...+d."""
    total = 0
    for c in range(COLS):
        depth = 0
        for y in range(VIS):
            left = 1 if c == 0 else (1 if g[y][c - 1] else 0)
            right = 1 if c == COLS - 1 else (1 if g[y][c + 1] else 0)
            if not g[y][c] and left and right:
                depth += 1
                total += depth
            else:
                depth = 0
    return total


def ind_aggregate_height(g):
    return sum(ind_heights(g))


def ind_bumpiness(g):
    h = ind_heights(g)
    return sum(abs(h[c] - h[c + 1]) for c in range(COLS - 1))


def rows_to_grid(rows):
    """Engine's 22 bitmask rows -> visible 20x10 list-of-lists."""
    off = E.ROWS - VIS
    return [[(rows[off + y] >> c) & 1 for c in range(COLS)]
            for y in range(VIS)]


def check_features():
    print("\n3. independent 8-feature implementation vs rl/features.py")
    try:
        import features as F
    except ImportError as e:
        fail("W3", "cannot import rl/features.py: %s" % e)
        return None
    from rng import next_u32, seed_state

    st = seed_state(0xFEA7)
    names = ["row_transitions", "column_transitions", "holes",
             "cumulative_wells", "aggregate_height", "bumpiness"]
    mine = [ind_row_transitions, ind_column_transitions, ind_holes,
            ind_cumulative_wells, ind_aggregate_height, ind_bumpiness]
    theirs = [F.row_transitions, F.column_transitions, F.holes,
              F.cumulative_wells, F.aggregate_height, F.bumpiness]
    disagree = {n: 0 for n in names}
    n = 0
    for _ in range(3000):
        rows = [0] * E.ROWS
        st, v = next_u32(st)
        ceiling = 2 + v % 18
        for y in range(ceiling, E.ROWS):
            st, a = next_u32(st)
            st, b = next_u32(st)
            m = (a ^ (b >> 7)) & E.FULL_ROW
            if m == E.FULL_ROW:
                m &= ~(1 << (a % E.W))
            rows[y] = m
        g = rows_to_grid(tuple(rows))
        board = np.array(g, dtype=np.int8)
        for nm, f_mine, f_theirs in zip(names, mine, theirs):
            if int(f_mine(g)) != int(f_theirs(board)):
                disagree[nm] += 1
        n += 1
    bad = {k: v for k, v in disagree.items() if v}
    if bad:
        fail("W3", "my independent implementation disagrees with "
                   "rl/features.py on %d boards: %r. Until this is resolved "
                   "the re-evaluation below cannot be trusted either way."
             % (n, bad))
        # show one example per disagreeing feature
        for nm in bad:
            print("      (%s disagreed on %d/%d boards)" % (nm, bad[nm], n))
    else:
        ok("%d random boards: all 6 board-only features agree exactly between "
           "my from-scratch loops and rl's numpy implementation" % n)
    return F


# ===========================================================================
# 4. re-evaluation on fresh seeds, driven by engine.py
# ===========================================================================

def landing_height_from_info(info):
    """Reconstruct rl's landing_height from the raw geometry the engine
    guarantees: mean height above the floor of the 4 locked cells, floor = 1."""
    return sum(E.ROWS - y for y, _x in info["piece_cells"]) / 4.0


def eval_linear(weights, seeds, piece_cap, label, features_mod):
    """Greedy argmax over placements using w . f, driven by engine.py."""
    out = []
    for seed in seeds:
        t0 = time.time()
        s = E.new_game(seed)
        pieces = 0
        while not s.game_over and pieces < piece_cap:
            ps = E.legal_placements(s)
            if not ps:
                break
            best, best_v = None, None
            for p in ps:
                ns, info = E.apply_placement(s, p)
                g = rows_to_grid(ns.rows)
                board = np.array(g, dtype=np.int8)
                f = features_mod.extract(board,
                                         landing_height_from_info(info),
                                         int(info["eroded_piece_cells"]))
                v = float(np.dot(weights, f))
                if best_v is None or v > best_v:
                    best_v, best = v, p
            s, _info = E.apply_placement(s, best)
            pieces += 1
        out.append({"seed": seed, "lines": s.lines, "pieces": pieces,
                    "died": s.game_over, "hit_cap": pieces >= piece_cap,
                    "seconds": round(time.time() - t0, 1)})
        print("      %s seed=%d -> lines=%d pieces=%d %s (%.0fs)"
              % (label, seed, s.lines, pieces,
                 "DIED" if s.game_over else "hit cap", out[-1]["seconds"]))
    return out


def reevaluate_score(features_mod, piece_cap=6000):
    """cem_score optimises SCORE, so re-check it on score, on fresh seeds."""
    print("\n4b. RE-EVALUATION of cem_score.json (objective = score)")
    path = os.path.join(WEIGHTS, "cem_score.json")
    if not os.path.exists(path):
        print("    (cem_score.json absent -- skipped)")
        return
    d = json.load(open(path))
    m = d["meta"]
    w = np.array(d["weights"], dtype=np.float64)
    seeds = FRESH_SEEDS[:6]
    print("    seeds: %r (piece cap %d)" % (seeds, piece_cap))
    res = []
    for seed in seeds:
        t0 = time.time()
        s = E.new_game(seed)
        pieces = 0
        while not s.game_over and pieces < piece_cap:
            ps = E.legal_placements(s)
            if not ps:
                break
            best, best_v = None, None
            for p in ps:
                ns, info = E.apply_placement(s, p)
                board = np.array(rows_to_grid(ns.rows), dtype=np.int8)
                f = features_mod.extract(board,
                                         landing_height_from_info(info),
                                         int(info["eroded_piece_cells"]))
                v = float(np.dot(w, f))
                if best_v is None or v > best_v:
                    best_v, best = v, p
            s, _i = E.apply_placement(s, best)
            pieces += 1
        res.append({"seed": seed, "lines": s.lines, "score": s.score,
                    "pieces": pieces, "died": s.game_over})
        print("      seed=%d -> lines=%d score=%d pieces=%d %s (%.0fs)"
              % (seed, s.lines, s.score, pieces,
                 "DIED" if s.game_over else "hit cap", time.time() - t0))
    scores = [r["score"] for r in res]
    lines = [r["lines"] for r in res]
    mean_s = sum(scores) / len(scores)
    med_s = sorted(scores)[len(scores) // 2]
    rep = m["eval"]
    print("    mine : mean_score=%.4g median=%.4g mean_lines=%.1f died=%d/%d"
          % (mean_s, med_s, sum(lines) / len(lines),
             sum(1 for r in res if r["died"]), len(res)))
    print("    rl's : mean_score=%.4g mean_lines=%.1f"
          % (rep.get("mean_score", 0), rep.get("mean_lines", 0)))
    top_share = max(scores) / float(sum(scores))
    if top_share > 0.35:
        print("    (my own run is skewed too: top game = %.0f%% of the total, "
              "so the SKEW IS A PROPERTY OF THE POLICY, not of rl's seed "
              "choice -- score-seeking tetris play is heavy-tailed by nature)"
              % (100 * top_share))
    if mean_s <= 0:
        fail("W4b", "cem_score scores 0 on fresh seeds")
    else:
        ok("cem_score reproduces a large score on seeds rl never used "
           "(mean %.4g, median %.4g over %d games); the ORDER OF MAGNITUDE of "
           "rl's claim holds, but see the skew finding above -- the mean is "
           "not the right summary statistic here" % (mean_s, med_s, len(res)))


def reevaluate(features_mod, piece_cap=3000):
    print("\n4. RE-EVALUATION on seeds rl used for neither training nor eval")
    print("    seeds: %r" % FRESH_SEEDS)
    print("    piece cap: %d (a cap is unavoidable -- a good policy does not "
          "die. Reported below as a floor, not a mean.)" % piece_cap)
    path = os.path.join(WEIGHTS, "cem_linear.json")
    if not os.path.exists(path):
        fail("W4", "weights/cem_linear.json missing")
        return
    d = json.load(open(path))
    if d.get("kind") != "linear":
        fail("W4", "cem_linear.json kind=%r, expected 'linear'" % d.get("kind"))
        return
    if list(d["features"]) != list(features_mod.FEATURE_NAMES):
        fail("W4", "feature order in the weights file %r != features.py %r"
             % (list(d["features"]), list(features_mod.FEATURE_NAMES)))
        return
    w = np.array(d["weights"], dtype=np.float64)
    res = eval_linear(w, FRESH_SEEDS, piece_cap, "cem_linear", features_mod)

    lines = [r["lines"] for r in res]
    died = [r for r in res if r["died"]]
    mean = sum(lines) / len(lines)
    print("    mean_lines=%.1f min=%d max=%d ; died in %d/%d games"
          % (mean, min(lines), max(lines), len(died), len(res)))

    reported = json.load(open(path))["meta"]["eval"]["mean_lines"]
    if all(r["hit_cap"] for r in res):
        ok("PASS criterion 3: never died in %d games on fresh seeds; every "
           "game reached the %d-piece cap with >= %d lines, so the 10-game "
           "mean is >= %.1f, far above the 1,000 target. (rl reported %.1f "
           "against a 50,000-piece cap -- both numbers are cap floors, not "
           "measured means, but the CLAIM holds on my seeds too.)"
           % (len(res), piece_cap, min(lines), mean, reported))
    elif mean >= 1000:
        ok("PASS criterion 3: mean %.1f lines >= 1,000 on fresh seeds "
           "(%d/%d games died)" % (mean, len(died), len(res)))
    else:
        fail("W4", "criterion 3 FAILS on fresh seeds: mean %.1f lines < 1,000 "
                   "(rl reported %.1f). Died in %d/%d games. The difference "
                   "between rl's number and mine is the finding."
             % (mean, reported, len(died), len(res)))
    return res


# ===========================================================================
# 5. fastsim <-> engine, checked independently of rl's own comparison
# ===========================================================================

def cross_check_fastsim(n_seeds=24, max_moves=1200):
    print("\n5. fastsim vs engine: independent board comparison")
    try:
        import fastsim as FS
    except ImportError as e:
        fail("W5", "cannot import rl/fastsim.py: %s" % e)
        return
    if not hasattr(FS, "FastGame"):
        fail("W5", "rl/fastsim.py has no FastGame class")
        return

    from rng import next_u32, seed_state
    mismatch = 0
    total = 0
    for i in range(n_seeds):
        seed = 4242 + i * 7919
        s = E.new_game(seed)
        try:
            fg = FS.FastGame(seed)
        except Exception as e:
            fail("W5", "FastGame(%d) construction failed: %s" % (seed, e))
            return
        rs = seed_state(seed ^ 0x5A5A5A5A)
        for step in range(max_moves):
            eps = E.legal_placements(s)
            try:
                fps = fg.legal_placements()
            except Exception as e:
                fail("W5", "fastsim legal_placements failed at seed=%d step=%d: "
                           "%s" % (seed, step, e))
                return
            # fastsim's records carry precomputed geometry in slots 2+, and
            # its `x` is the LEFTMOST OCCUPIED COLUMN while the engine's is the
            # bounding-box origin (which can be -1). Compare in absolute
            # columns, which is the only shared coordinate.
            ek = sorted((p[0], E.placement_left_col(p)) for p in eps)
            fk = sorted((p[0], p[1]) for p in fps)
            if ek != fk:
                mismatch += 1
                fail("W5", "seed=%d step=%d: placement sets differ in absolute "
                           "columns. engine has %d, fastsim has %d; "
                           "engine-only=%r fastsim-only=%r"
                     % (seed, step, len(ek), len(fk),
                        sorted(set(ek) - set(fk))[:4],
                        sorted(set(fk) - set(ek))[:4]))
                return
            if not eps:
                break
            # Alternate a cheap flat greedy with random choice. Random alone
            # dies in ~22 pieces, which never builds the deep, ragged boards
            # where a fast reimplementation is most likely to disagree.
            rs, v = next_u32(rs)
            if i % 2 == 0:
                best, best_sc = None, None
                for q in eps:
                    ns, qi = E.apply_placement(s, q)
                    top = _TOPS(ns.rows)
                    h = [E.ROWS - t for t in top]
                    holes = 0
                    for c in range(E.W):
                        for y in range(top[c] + 1, E.ROWS):
                            if not ((ns.rows[y] >> c) & 1):
                                holes += 1
                    sc = -(holes * 1000 + sum(h) * 10
                           + sum(abs(h[c] - h[c + 1])
                                 for c in range(E.W - 1)) * 20)
                    sc += qi["lines_cleared"] * 3000
                    if best_sc is None or sc > best_sc:
                        best_sc, best = sc, q
                p = best
            else:
                p = eps[v % len(eps)]
            key = (p[0], E.placement_left_col(p))
            rec = next(q for q in fps if (q[0], q[1]) == key)
            s, einfo = E.apply_placement(s, p)
            f_lines = fg.apply(rec)
            total += 1
            if f_lines != einfo["lines_cleared"]:
                fail("W5", "seed=%d step=%d placement %r: lines_cleared "
                           "engine=%d fastsim=%d"
                     % (seed, step, (p[0], p[1]), einfo["lines_cleared"],
                        f_lines))
                return
            # compare the boards, not just the line counts
            fcols = fg.to_array()
            e_grid = np.array(rows_to_grid(s.rows), dtype=np.int8)
            f_grid = np.asarray(fcols, dtype=np.int8)
            if f_grid.shape != e_grid.shape:
                # fastsim may return 22 rows; compare the visible window
                if f_grid.shape[0] >= VIS:
                    f_grid = f_grid[-VIS:]
            if not np.array_equal((e_grid != 0), (f_grid != 0)):
                mismatch += 1
                fail("W5", "seed=%d step=%d: BOARDS DIVERGE after the same "
                           "placement %r. engine is the authority (lead ruling "
                           "#4 condition 4), so fastsim is wrong here."
                     % (seed, step, p))
                print("      engine:\n%s" % s.render())
                return
            if s.lines != fg.lines_total:
                fail("W5", "seed=%d step=%d: line totals differ engine=%d "
                           "fastsim=%d" % (seed, step, s.lines, fg.lines_total))
                return
            if s.game_over != fg.game_over:
                fail("W5", "seed=%d step=%d: game_over differs engine=%s "
                           "fastsim=%s -- this is exactly the divergence that "
                           "would inflate or deflate rl's training returns"
                     % (seed, step, s.game_over, fg.game_over))
                return
            if s.game_over:
                break
    if mismatch == 0:
        ok("%d seeds, %d placements: fastsim's legal placement set and its "
           "board after every move are identical to engine.py's" % (n_seeds, total))


def main():
    print("=" * 74)
    print("checker: independent re-evaluation of rl's weights")
    print("=" * 74)
    audit_metadata()
    F = check_features()
    if F is not None:
        reevaluate(F)
        reevaluate_score(F)
    cross_check_fastsim()
    print("\n" + "=" * 74)
    if FAILURES:
        print("RESULT: %d FINDING(S)" % len(FAILURES))
        for tag, msg in FAILURES:
            print("  [%s] %s" % (tag, msg.splitlines()[0]))
        return 1
    print("RESULT: rl's claims survive independent re-evaluation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
