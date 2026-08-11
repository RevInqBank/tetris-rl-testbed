"""CEM (cross-entropy method) for a linear afterstate policy.

WHERE THIS SITS ON THE STUDY-PLAN AXIS
--------------------------------------
Nowhere -- and that is the point. The axis the study plan follows is "where does
the critic enter the update rule?", running REINFORCE -> +baseline -> A2C -> DQN.
CEM has no critic, no value function, no gradient, and no temporal-difference
anything. It treats the whole game as a black box that returns one number (lines
cleared) for one weight vector, and it searches weight space directly.

It is in this project as the CONTROL that hurts: it is expected to beat all four
critic-based methods by a wide margin. That result is worth showing honestly,
because it says the credit-assignment machinery the study plan is about buys you
nothing when the policy class is 8 numbers and rollouts are cheap. The critic
earns its keep when the policy is large and samples are expensive -- neither of
which is true here.

THE POLICY
----------
    score(placement) = w . f(afterstate)          w in R^8
    action           = argmax over legal placements

Note that argmax is invariant to positive rescaling of w, so the weight vector
only has 7 meaningful degrees of freedom (a direction on the unit sphere). The
search normalises ||w|| = 1 after every update to stop the distribution from
wandering off to arbitrarily large magnitudes, which would otherwise make sigma
meaningless.

THE SEARCH
----------
Keep a diagonal Gaussian over weight vectors, N(mu, diag(sigma^2)).

    1. sample `n_candidates` weight vectors
    2. score each by playing `n_seeds` games and averaging lines cleared
    3. keep the best `elite_frac`; set mu, sigma to the elite mean and std
    4. add extra noise Z_t to sigma^2, decaying with generation

Step 4 is from Szita & Lorincz (2006), and it is not optional. Without it the
elite variance collapses within a few generations, the distribution freezes on a
mediocre direction, and the run flatlines. Z_t = max(0, z_start - t / z_decay)
keeps the search alive early and lets it converge late.

COMMON RANDOM NUMBERS
---------------------
Within one generation every candidate is evaluated on the SAME set of seeds.
Comparing candidates on different games would mean the elite set is picking the
luckiest piece sequences rather than the best weights. The seed set is redrawn
each generation so the weights cannot overfit any particular sequence.

The seeds used during training come from a training range; final evaluation uses
a disjoint range (see `evaluate.py`). This is checked -- `checker` will re-run on
seeds this file never touched.

PIECE CAP
---------
A rollout is cut off after `max_pieces`. A policy that has learned not to die
does not stop on its own, and one immortal candidate would hang the generation.
The cap starts low (fast, noisy generations while the search is far from good)
and grows, because once candidates all survive to the cap, lines-cleared
saturates and the elite selection loses its ability to discriminate.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import time
from datetime import datetime, timezone

import numpy as np

from fastsim import (PLACEMENTS, TOTAL_ROWS, BagRandomizer, board_features_bits,
                     level_for, score_clear, simulate, spawn_blocked,
                     well_extras_bits)
from features import (DEFAULT_FEATURE_SET, FEATURE_SETS, SCORE_EPS,
                      argmax_stable, feature_scale)

N_FEATURES = 8


# ---------------------------------------------------------------------------
# Policy shapes
# ---------------------------------------------------------------------------

def mlp_param_count(d_in, hidden):
    """Length of the flat parameter vector for a d_in -> hidden -> 1 net."""
    return d_in * hidden + hidden + hidden + 1


def unflatten_mlp(theta, d_in, hidden):
    """Flat CEM parameter vector -> (W1, b1, W2, b2).

    CEM searches a flat vector; the net is just how that vector is READ. There
    is no gradient here -- `nn.py` is not involved, only its shape convention
    (W stored as (in, out), ReLU hidden, linear scalar head) so the JSON the
    browser loads is the same format the policy-gradient nets use.
    """
    i = d_in * hidden
    W1 = theta[:i].reshape(d_in, hidden)
    b1 = theta[i:i + hidden]
    i += hidden
    W2 = theta[i:i + hidden].reshape(hidden, 1)
    b2 = theta[i + hidden:i + hidden + 1]
    return W1, b1, W2, b2


def linear_as_mlp(w, hidden):
    """Flat MLP parameters that compute EXACTLY the linear score w . x.

    This exists to separate two explanations of a poor MLP result:
        (a) a non-linear policy does not help on this problem
        (b) CEM cannot find good parameters in this many dimensions

    ReLU can reproduce the identity with a pair of units, since
    relu(z) - relu(-z) = z. So with hidden >= 2 the MLP class CONTAINS every
    linear policy, and the linear optimum is reachable. If CEM then returns
    something clearly WORSE than the linear policy, the class was not the
    limitation -- the search was. That makes (b) the live hypothesis.

    Requires hidden >= 2; the remaining units are zeroed.
    """
    d_in = len(w)
    theta = np.zeros(mlp_param_count(d_in, hidden))
    W1, b1, W2, b2 = unflatten_mlp(theta, d_in, hidden)
    W1[:, 0] = w            # unit 0:  relu( w.x)
    W1[:, 1] = -np.asarray(w)   # unit 1: relu(-w.x)
    W2[0, 0] = 1.0
    W2[1, 0] = -1.0         # difference recovers w.x for either sign
    return theta

# Two objectives, two very different policies. See the module docstring section
# "TWO OBJECTIVES" below.
OBJECTIVES = ("lines", "score", "score_safe", "score_gated", "score_penalty")

# score_penalty needs a constant; the other objectives do not. That is the
# reason score_safe is the default of the three: an arbitrary lambda is a knob
# nobody can justify from the problem.
DEATH_PENALTY_LAMBDA = 20.0


# ---------------------------------------------------------------------------
# Rollout
# ---------------------------------------------------------------------------

def rollout(w, seed: int, max_pieces: int = 500, search: bool = False,
            wells: bool = False, mlp=None):
    """Play one game greedily under weight vector `w`.

    Returns a stats dict. Kept as a flat loop over ints rather than a Game
    object: this is the innermost loop of the project and attribute lookups
    showed up clearly in profiling.

    Scoring (score_delta, back-to-back, combo, level) is tracked even when the
    objective is lines, so every run reports both axes and the two policies can
    be compared on the same numbers.

    `mlp=(d_in, hidden, scale)` reads `w` as flat network parameters instead of
    linear weights. Every candidate placement is scored in ONE batched forward
    pass -- per-placement numpy calls cost more in dispatch than the arithmetic.

    `search=True` selects moves with 2-ply lookahead instead of greedy argmax.
    This exists so CEM can fit weights FOR the search policy rather than for the
    greedy one. Measured: weights fitted greedily and then used inside a search
    LOSE to the same weights used greedily (49.33 vs 46.56 normalised score per
    piece), because the value function was never asked to be good at depth 2.
    Costs ~25x per placement, so callers must shrink the piece cap.
    """
    w0, w1, w2, w3, w4, w5, w6, w7 = (float(x) for x in w[:8])
    # wells10 appends max_well_depth, well_count. Unpacking w[:8] keeps the
    # 8-feature path byte-identical, which is what makes the invariance check
    # ("do the existing policies score exactly the same?") meaningful.
    w8, w9 = (float(w[8]), float(w[9])) if wells else (0.0, 0.0)

    cols = [0] * 10
    top = [TOTAL_ROWS] * 10
    bag = BagRandomizer(seed)
    current = bag.next_piece()

    lines_total = 0
    score_total = 0
    score_norm = 0.0
    pieces = 0
    b2b = 0
    combo = 0
    tetris_lines = 0
    died = True

    while pieces < max_pieces:
        # The engine ends the game when a piece cannot spawn, which can happen
        # while legal hard drops still exist elsewhere on the board.
        if spawn_blocked(cols, current):
            break

        if mlp is not None:
            d_in, hidden, scale = mlp
            W1, b1, W2, b2 = unflatten_mlp(w, d_in, hidden)
            feats, results = [], []
            for rec in PLACEMENTS[current]:
                res = simulate(cols, top, rec)
                if res is None:
                    continue
                ncols, ntop, lines, lh, er = res
                rt, ct, ho, cw, agg, bump = board_features_bits(ncols)
                row = [lh, er, rt, ct, ho, cw, agg, bump]
                if wells:
                    row.extend(well_extras_bits(ncols))
                feats.append(row)
                results.append(res)
            if not results:
                break
            X = np.asarray(feats) / scale
            scores = (np.maximum(X @ W1 + b1, 0.0) @ W2 + b2)[:, 0]
            best = results[argmax_stable(scores)]
        elif search:
            from search import best_placement_1ply
            _rec, best = best_placement_1ply(w, cols, top, current,
                                             bag.peek_next(), rule="leaf")
        else:
            best_score = -1e30
            best = None
            for rec in PLACEMENTS[current]:
                res = simulate(cols, top, rec)
                if res is None:
                    continue                  # would stick out above the board
                ncols, ntop, lines, lh, er = res
                rt, ct, ho, cw, agg, bump = board_features_bits(ncols)
                # Epsilon comparison, not rounding: Python's round() and JS's
                # Math.round() disagree on negative ties. See
                # features.argmax_stable.
                s = (w0 * lh + w1 * er + w2 * rt + w3 * ct
                     + w4 * ho + w5 * cw + w6 * agg + w7 * bump)
                if wells:
                    mwd, wc = well_extras_bits(ncols)
                    s += w8 * mwd + w9 * wc
                if s > best_score + SCORE_EPS:
                    best_score = s
                    best = res
        if best is None:
            break                              # nothing fits -> dead

        cols, top, lines, _lh, _er = best
        level = level_for(lines_total)
        delta, b2b, combo, _applied = score_clear(lines, level, b2b, combo)
        score_total += delta
        # Level-normalised: the same clear is worth the same whenever it
        # happens. See `fitness` for why this, not raw score, is the target.
        score_norm += delta / level
        lines_total += lines
        if lines == 4:
            tetris_lines += 4
        pieces += 1
        current = bag.next_piece()
    else:
        died = False                           # stopped at the cap, not dead

    return {
        "lines": lines_total,
        "score": score_total,
        "pieces": pieces,
        "died": died,
        "score_per_piece": score_total / pieces if pieces else 0.0,
        "norm_score_per_piece": score_norm / pieces if pieces else 0.0,
        "tetris_rate": tetris_lines / lines_total if lines_total else 0.0,
    }


def fitness(stats, objective: str, piece_cap: int = 0) -> float:
    """The single number CEM maximises.

    lines : total lines cleared. Saturates once the policy stops dying -- every
        candidate hits the piece cap and the elite selection goes blind.

    score : LEVEL-NORMALISED score per piece -- mean over pieces of
        (score_delta / level). Two divisions, both load-bearing.

        Divide by PIECES because total score rewards surviving longer, and
        survival is the axis that already saturated.

        Divide by LEVEL because level = 1 + lines/10 climbs all game and
        multiplies every clear. Without it, a single cleared at level 60 scores
        6000 while a tetris at level 1 scores 800 -- so the fitness is really
        measuring "how long did you survive before scoring", smuggling the
        survival objective straight back in. This was not hypothetical: the
        first score run hit 1,546 raw score/piece with a 0.1% tetris rate,
        having simply learned to survive and clear singles.

        Level-normalised, the ratios are the ones the player actually feels:
            single 100 | double 300 | triple 500 | tetris 800
            tetris after tetris 1200 (back-to-back x1.5), plus 50 x combo
        so a tetris is worth 8-12 singles and the objective finally points at
        "터뜨릴 때 팍 터뜨린다".

        No survival bonus. Dying stops the scoring, so survival is already a
        constraint; paying for it too rebuilds the immortal line-shaver.

    THAT LAST PARAGRAPH TURNED OUT TO BE WRONG, and score_safe is the fix.
    Dividing by pieces means an early death is not actually punished: a run that
    scores well for 700 pieces and dies looks as good as one that scores the
    same for 3,000 and lives. `cem_score_wells` reached the top score on this
    axis while dying in 4 of 10 games. Survival stops the scoring but it does
    not stop the AVERAGE.

    score_safe : score_per_piece x min(1, pieces / piece_cap)

        Multiplicative, so an early death scales the fitness down in proportion
        and a run that reaches the cap keeps its score untouched. A candidate
        that scores highly and dies early is pushed below one that scores nearly
        as well and survives -- which is the actual goal, "a decent score
        without dying".

        Chosen over the two alternatives below because it introduces NO
        arbitrary constant: no threshold, no lambda to tune. Every number in it
        comes from the run itself.

    score_gated : score_per_piece if the run reached the cap, else 0.
        A harder constraint. The risk is a sparse signal early on -- before any
        candidate survives, every fitness is 0 and the elite selection has
        nothing to rank.

    score_penalty : score_per_piece - lambda x died.
        Needs lambda picked by hand, which is exactly the kind of knob this
        project has been removing.
    """
    if objective == "lines":
        return float(stats["lines"])
    spp = float(stats["norm_score_per_piece"])
    if objective == "score":
        return spp
    survived = (min(1.0, stats["pieces"] / piece_cap) if piece_cap else
                (0.0 if stats["died"] else 1.0))
    if objective == "score_safe":
        return spp * survived
    if objective == "score_gated":
        return 0.0 if stats["died"] else spp
    if objective == "score_penalty":
        return spp - DEATH_PENALTY_LAMBDA * (1.0 if stats["died"] else 0.0)
    raise ValueError(f"unknown objective {objective}")


def _eval_candidate(job):
    """Worker entry point: mean fitness for one candidate over a seed set."""
    w, seeds, max_pieces, objective, search, wells, mlp = job
    total = 0.0
    for s in seeds:
        total += fitness(rollout(w, s, max_pieces, search, wells, mlp),
                         objective, max_pieces)
    return total / len(seeds)


# ---------------------------------------------------------------------------
# CEM
# ---------------------------------------------------------------------------

def train_cem(
    n_candidates: int = 64,
    elite_frac: float = 0.15,
    n_seeds: int = 8,
    generations: int = 60,
    max_pieces_schedule=(200, 500, 1500, 5000),
    search_max_pieces_schedule=(100, 200, 400, 800),
    z_start: float = 4.0,
    z_decay: float = 12.0,
    sigma_min: float = 0.02,
    search: bool = False,
    feature_set: str = DEFAULT_FEATURE_SET,
    hidden: int = 0,
    time_budget_s: float = 900.0,
    objective: str = "lines",
    ckpt_path: str | None = None,
    curve_path: str | None = None,
    resume: bool = False,
    train_seed_lo: int = 1,
    train_seed_hi: int = 100_000,
    rng_seed: int = 20260807,
    n_workers: int | None = None,
    verbose: bool = True,
):
    """Run CEM and return (best_weights, history).

    `history` is a list of per-generation dicts -- the honest learning curve,
    saved into the weights file so nobody has to take a summary statistic on
    trust.
    """
    names = FEATURE_SETS[feature_set]
    d_in = len(names)
    wells = "max_well_depth" in names
    # hidden > 0 switches the policy from linear to a d_in -> hidden -> 1 net.
    # CEM then searches the flat parameter vector, which is far longer: 10
    # weights becomes 97 at hidden=8. Nothing else about the search changes.
    mlp = (d_in, hidden, feature_scale(names)) if hidden else None
    n_features = mlp_param_count(d_in, hidden) if hidden else d_in
    rng = np.random.default_rng(rng_seed)
    n_elite = max(2, int(round(n_candidates * elite_frac)))
    if n_workers is None:
        n_workers = min(64, os.cpu_count() or 8)

    # Start from an uninformed spherical prior. Deliberately NOT seeded with
    # Dellacherie's weights: seeding would make a good result unsurprising and
    # would not show that the search can find the structure on its own.
    mu = np.zeros(n_features)
    sigma = np.full(n_features, 1.0)

    history = []
    best_ever_w = mu.copy()
    best_ever_score = -1e30
    gen0 = 0
    collapsed = None                 # generation at which sigma died, if ever
    collapse_threshold = 1e-6
    # curve_*.jsonl is append-only, and generation numbers restart every run.
    # Without a run id, "collapse at generation N" reads across runs and is
    # wrong. Derived from the config so a --resume continues the same id.
    run_id = (f"{objective}-c{n_candidates}-s{n_seeds}-b{int(time_budget_s)}"
              f"-sm{sigma_min}-{'search' if search else 'greedy'}")

    # Resume: (mu, sigma) IS the entire search state, so the checkpoint is tiny
    # and restarting is exact apart from the RNG stream.
    if resume and ckpt_path and os.path.exists(ckpt_path):
        with open(ckpt_path) as f:
            ck = json.load(f)
        mu = np.array(ck["mu"])
        sigma = np.array(ck["sigma"])
        best_ever_w = np.array(ck["best_weights"])
        best_ever_score = ck["best_fitness"]
        gen0 = ck["generation"] + 1
        history = ck.get("history", [])
        rng = np.random.default_rng(rng_seed + gen0)
        if verbose:
            print(f"[cem] resumed from {ckpt_path} at generation {gen0}")

    t_start = time.perf_counter()

    pool = mp.Pool(processes=n_workers)
    try:
        for gen in range(gen0, generations):
            elapsed = time.perf_counter() - t_start
            if elapsed > time_budget_s:
                if verbose:
                    print(f"[cem] time budget {time_budget_s:.0f}s reached at gen {gen}")
                break

            # Piece cap grows as the run progresses. Keyed on ELAPSED TIME,
            # not generation index: --generations now defaults to effectively
            # unlimited so that --minutes is the real budget, which made a
            # generation-based fraction stay pinned at 0 and freeze the cap at
            # its smallest value for the whole run. Caught when a search run
            # spent 14 minutes at cap 100 and never learned to survive longer.
            frac = min(0.999, elapsed / time_budget_s) if time_budget_s > 0 else 0.0
            sched = search_max_pieces_schedule if search else max_pieces_schedule
            cap = sched[min(int(frac * len(sched)), len(sched) - 1)]

            # Common random numbers within the generation, fresh across them.
            seeds = rng.integers(train_seed_lo, train_seed_hi, size=n_seeds).tolist()

            cands = rng.normal(mu, np.maximum(sigma, 1e-8),
                               size=(n_candidates, n_features))

            scores = np.array(pool.map(
                _eval_candidate,
                [(cands[i], seeds, cap, objective, search, wells, mlp)
                 for i in range(n_candidates)],
                chunksize=1,
            ))

            elite_idx = np.argsort(scores)[-n_elite:]
            elite = cands[elite_idx]

            mu = elite.mean(axis=0)
            # Extra noise Z_t keeps the distribution from collapsing early.
            z = max(0.0, z_start - gen / z_decay)
            sigma = np.sqrt(elite.var(axis=0) + z)

            # Fix the scale gauge: argmax only cares about the direction of w.
            norm = np.linalg.norm(mu)
            if norm > 1e-9:
                mu = mu / norm
                sigma = sigma / norm

            # VARIANCE FLOOR. Without this the search dies silently.
            # Measured on a 60-minute run: sigma_norm hit exactly 0 at
            # generation 168 (~3 minutes) and stayed there for the remaining
            # 197 generations. Once sigma is 0 every candidate is the SAME
            # weight vector, so the elite selection has nothing to choose
            # between and no learning is possible -- the elite score keeps
            # wobbling only because the seed set changes each generation, which
            # reads like progress and is not. Z_t decays to 0 by design, so it
            # cannot be what keeps the search alive at the end.
            sigma = np.maximum(sigma, sigma_min)

            gen_best = float(scores.max())
            if gen_best > best_ever_score:
                best_ever_score = gen_best
                best_ever_w = cands[int(np.argmax(scores))].copy()
                n = np.linalg.norm(best_ever_w)
                if n > 1e-9:
                    best_ever_w /= n

            rec = {
                "run_id": run_id,
                "generation": gen,
                "objective": objective,
                "piece_cap": cap,
                "seeds": seeds,
                "mean_fitness": float(scores.mean()),
                "elite_mean_fitness": float(scores[elite_idx].mean()),
                "best_fitness": gen_best,
                "sigma_norm": float(np.linalg.norm(sigma)),
                "elapsed_s": round(time.perf_counter() - t_start, 1),
            }
            history.append(rec)
            if not collapsed and rec["sigma_norm"] < collapse_threshold:
                collapsed = gen
                print(f"[cem] *** SEARCH DISTRIBUTION COLLAPSED at generation "
                      f"{gen} (|sigma| = {rec['sigma_norm']:.2e}) ***")
                print(f"[cem]     Every candidate is now the same weight vector; "
                      f"later generations cannot learn.")
                print(f"[cem]     Elite score will still move -- that is the "
                      f"seed set changing, not progress.")
                print(f"[cem]     Raise --sigma-min (currently {sigma_min}) "
                      f"or shorten the run.")
            if ckpt_path:      # every generation: a killed run resumes here
                from artifacts import dump_json
                dump_json(ckpt_path, {
                    "objective": objective, "generation": gen,
                    "mu": mu.tolist(), "sigma": sigma.tolist(),
                    "best_weights": best_ever_w.tolist(),
                    "best_fitness": best_ever_score,
                    "rng_seed": rng_seed, "history": history,
                })
            if curve_path:     # append-only: survives even if ckpt does not
                os.makedirs(os.path.dirname(os.path.abspath(curve_path)), exist_ok=True)
                with open(curve_path, "a") as cf:
                    cf.write(json.dumps(rec) + "\n")
            if verbose:
                print(f"[cem/{objective}] gen {gen:3d} cap {cap:5d} | "
                      f"mean {rec['mean_fitness']:9.2f} | "
                      f"elite {rec['elite_mean_fitness']:9.2f} | "
                      f"best {gen_best:9.2f} | "
                      f"|sigma| {rec['sigma_norm']:6.3f} | "
                      f"{rec['elapsed_s']:6.1f}s")
    finally:
        pool.close()
        pool.join()

    # The distribution mean is normally the better bet than the single luckiest
    # candidate (which is partly seed luck), but verify rather than assume --
    # the caller re-evaluates both on held-out seeds.
    if verbose:
        if collapsed is not None:
            print(f"[cem] NOTE: distribution collapsed at generation {collapsed} "
                  f"of {len(history)}; only the first {collapsed} generations "
                  f"were actually searching.")
        else:
            print(f"[cem] search stayed alive for all {len(history)} generations "
                  f"(final |sigma| = {history[-1]['sigma_norm']:.4f})"
                  if history else "[cem] no generations ran")
    return mu, best_ever_w, history, collapsed


# ---------------------------------------------------------------------------
# Held-out evaluation
# ---------------------------------------------------------------------------

def evaluate_weights(w, seeds, max_pieces=100_000, n_workers=None, search=False,
                     wells=False, mlp=None):
    """Play one game per seed; report BOTH axes.

    Lines and score are always both reported, whichever objective was trained,
    so the line policy and the score policy are compared on identical columns.
    This is a quick in-training check -- the authoritative table comes from
    `evaluate.py`, which runs on the engine.
    """
    if n_workers is None:
        n_workers = min(len(seeds), os.cpu_count() or 8)
    with mp.Pool(processes=n_workers) as pool:
        results = pool.starmap(rollout,
                               [(w, s, max_pieces, search, wells, mlp)
                                for s in seeds])
    lines = [r["lines"] for r in results]
    spp = [r["score_per_piece"] for r in results]
    return {
        "seeds": list(seeds),
        "lines": lines,
        "score": [r["score"] for r in results],
        "pieces": [r["pieces"] for r in results],
        "hit_cap": [not r["died"] for r in results],
        "mean_lines": float(np.mean(lines)),
        "min_lines": int(np.min(lines)),
        "max_lines": int(np.max(lines)),
        "mean_score": float(np.mean([r["score"] for r in results])),
        "mean_score_per_piece": float(np.mean(spp)),
        "mean_norm_score_per_piece": float(np.mean([r["norm_score_per_piece"] for r in results])),
        "mean_score_safe": float(np.mean(
            [r["norm_score_per_piece"] * min(1.0, r["pieces"] / max_pieces)
             for r in results])),
        "survival_ratio": float(np.mean(
            [min(1.0, r["pieces"] / max_pieces) for r in results])),
        "mean_tetris_rate": float(np.mean([r["tetris_rate"] for r in results])),
        "evaluated_on": "fastsim (in-training check; evaluate.py is authoritative)",
    }


def save_weights(path, name, w, history, eval_result, extra_meta=None,
                 feature_set=DEFAULT_FEATURE_SET, hidden=0):
    if hidden:
        W1, b1, W2, b2 = unflatten_mlp(np.asarray(w, dtype=np.float64),
                                       len(FEATURE_SETS[feature_set]), hidden)
    payload = {
        "name": name,
        "kind": "mlp" if hidden else "linear",
        "features": list(FEATURE_SETS[feature_set]),
        "feature_set": feature_set,
        **({"layers": [{"W": W1.tolist(), "b": b1.tolist()},
                       {"W": W2.tolist(), "b": b2.tolist()}],
            "activation": "relu",
            # Networks eat normalised features; linear policies do not. The
            # browser must divide by this before the forward pass.
            "feature_scale": feature_scale(FEATURE_SETS[feature_set]).tolist()}
           if hidden else {"weights": [float(x) for x in w]}),
        "meta": {
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "algorithm": "cross-entropy method (no critic, no gradient)",
            "critic_role": "없음 — 진화전략이 가중치 공간을 직접 탐색한다. "
                           "가치함수도, 경사도, 시간차도 없다.",
            "episodes": sum(1 for _ in history),
            "history": history,
            "eval": eval_result,
            # ALWAYS present, and false until something actually verifies it.
            # `null` is worse than `false`: it reads as "unknown provenance",
            # and a weights file whose provenance is unknown should not reach a
            # user. `artifacts.py --parity run` raises this to true after the
            # engine comparison passes. Set here rather than per-trainer so no
            # future training path can forget it.
            "parity_verified": False,
            **(extra_meta or {}),
        },
    }
    from artifacts import dump_json
    dump_json(path, payload)
    return payload


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--objective", choices=OBJECTIVES, default="lines",
                    help="lines = total lines (saturates once immortal); "
                         "score = engine score per piece (rewards tetrises)")
    ap.add_argument("--generations", type=int, default=100000,
                    help="hard cap on generations. Default is effectively "
                         "unlimited so that --minutes is the real budget; set "
                         "it only if you want a generation count, not a time")
    ap.add_argument("--candidates", type=int, default=64)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--minutes", type=float, default=None,
                    help="wall-clock budget in minutes (overrides --time-budget). "
                         "NOTE: training stops at whichever comes first, this or "
                         "--generations")
    ap.add_argument("--features", choices=sorted(FEATURE_SETS),
                    default=DEFAULT_FEATURE_SET,
                    help="dellacherie8 (frozen default) or wells10 "
                         "(+max_well_depth, well_count). A new set produces a "
                         "new file; existing 8-feature files are untouched")
    ap.add_argument("--hidden", type=int, default=0,
                    help="0 = linear policy (default). >0 makes the policy a "
                         "d_in -> hidden -> 1 ReLU net whose flat parameters "
                         "CEM searches. Parameter count grows fast: 10 weights "
                         "-> 97 at hidden=8, 49 at hidden=4")
    ap.add_argument("--search", action="store_true",
                    help="fit the weights FOR a 2-ply search policy: rollouts "
                         "select moves by lookahead instead of greedy argmax. "
                         "~25x slower per placement, so lower --eval-cap and "
                         "the piece-cap schedule accordingly")
    ap.add_argument("--sigma-min", type=float, default=0.02,
                    help="variance floor. Below this the search distribution "
                         "collapses to a point and nothing more is learned")
    ap.add_argument("--time-budget", type=float, default=900.0)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--eval-cap", type=int, default=100_000)
    ap.add_argument("--resume", action="store_true",
                    help="continue from weights/ckpt_cem_<objective>.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    wdir = os.path.join(here, "..", "weights")
    default_name = {"lines": "cem_linear", "score": "cem_score"}.get(
        args.objective, "cem_" + args.objective)
    if args.features != DEFAULT_FEATURE_SET:
        default_name += "_" + args.features
    if args.hidden:
        default_name += f"_mlp{args.hidden}"
    out = args.out or os.path.join(wdir, default_name + ".json")
    # The search and greedy runs of the same objective are DIFFERENT searches
    # and must not share a checkpoint. They did, and the greedy run's
    # distribution mean was overwritten by the search run -- which is why
    # cem_linear can no longer be re-selected from its checkpoint.
    tag = (f"{args.objective}{'_search' if args.search else ''}"
           f"{'' if args.features == DEFAULT_FEATURE_SET else '_' + args.features}"
           f"{'' if not args.hidden else f'_mlp{args.hidden}'}")
    ckpt = os.path.join(wdir, f"ckpt_cem_{tag}.json")
    curve = os.path.join(wdir, f"curve_cem_{tag}.jsonl")
    budget = args.minutes * 60.0 if args.minutes else args.time_budget

    # Mirrors train_cem's defaults so the recorded schedule is the real one.
    max_pieces_schedule = (200, 500, 1500, 5000)
    search_max_pieces_schedule = (100, 200, 400, 800)

    print(f"[cem] objective={args.objective} | {args.candidates} candidates x "
          f"{args.seeds} seeds | budget {budget:.0f}s | "
          f"workers {args.workers or os.cpu_count()}")
    print(f"[cem] checkpoint {ckpt}")
    mu, best_w, history, collapsed = train_cem(
        n_candidates=args.candidates,
        sigma_min=args.sigma_min,
        search=args.search,
        n_seeds=args.seeds,
        generations=args.generations,
        time_budget_s=budget,
        objective=args.objective,
        ckpt_path=ckpt,
        curve_path=curve,
        resume=args.resume,
        n_workers=args.workers,
        feature_set=args.features,
        hidden=args.hidden,
    )
    if collapsed is not None:
        print(f"[cem] WARNING: only {collapsed}/{len(history)} generations "
              f"actually searched (distribution collapsed).")

    # Held-out seeds: 900000+ is disjoint from the training range (1..100000).
    holdout = list(range(900_001, 900_011))
    print("\n[cem] evaluating distribution mean on held-out seeds ...")
    names_ = FEATURE_SETS[args.features]
    wells_on = "max_well_depth" in names_
    mlp_cfg = ((len(names_), args.hidden, feature_scale(names_))
               if args.hidden else None)
    ev_mu = evaluate_weights(mu, holdout, max_pieces=args.eval_cap,
                             search=args.search, wells=wells_on, mlp=mlp_cfg)
    print(f"       lines {ev_mu['mean_lines']:.1f} | score/piece "
          f"{ev_mu['mean_norm_score_per_piece']:.1f} | tetris "
          f"{ev_mu['mean_tetris_rate'] * 100:.1f}%")
    print("[cem] evaluating best single candidate on held-out seeds ...")
    ev_best = evaluate_weights(best_w, holdout, max_pieces=args.eval_cap,
                               search=args.search, wells=wells_on, mlp=mlp_cfg)
    print(f"       lines {ev_best['mean_lines']:.1f} | score/piece "
          f"{ev_best['mean_norm_score_per_piece']:.1f} | tetris "
          f"{ev_best['mean_tetris_rate'] * 100:.1f}%")

    # Pick on the objective actually trained, not always on lines.
    # Select on the same quantity that was trained, or the choice contradicts
    # the objective.
    key = {"lines": "mean_lines",
           "score": "mean_norm_score_per_piece"}.get(
        args.objective, "mean_score_safe")
    if ev_mu[key] >= ev_best[key]:
        chosen, ev, which = mu, ev_mu, "distribution_mean"
    else:
        chosen, ev, which = best_w, ev_best, "best_single_candidate"
    print(f"\n[cem] selected: {which}")

    save_weights(out, default_name, chosen, history, ev,
                 feature_set=args.features, hidden=args.hidden, extra_meta={
        "objective": args.objective,
        "selected": which,
        # TRAINING and EVALUATION caps are different numbers and must not be
        # confused. Training rollouts are capped low because a generation is
        # 64 candidates x 8 seeds and an immortal candidate would never return;
        # evaluation is capped high because the cap IS the claim -- "does not
        # die" only ever means "does not die within this many pieces".
        "train_piece_cap_schedule": list(
            search_max_pieces_schedule if args.search else max_pieces_schedule),
        "eval_piece_cap": args.eval_cap,
        "cap_note": ("hit_cap_count 는 '안 죽음'이 아니라 "
                     f"'{args.eval_cap:,}조각까지 안 죽음'이다. 상한이 곧 주장의 범위다."),
        "train_seed_range": [1, 100000],
        "holdout_seed_range": [900001, 900010],
        "eval_distribution_mean": ev_mu,
        "eval_best_candidate": ev_best,
        "generations_run": len(history),
        "collapsed_at_generation": collapsed,
        "sigma_min": args.sigma_min,
        "wall_clock_budget_s": budget,
        "trained_with_search": args.search,
        "feature_set": args.features,
        "hidden": args.hidden,
        "policy_class": "mlp" if args.hidden else "linear",
        "n_parameters": len(chosen),
        **({"search": {
            "plies": 2, "uses_next_piece": True, "rule": "leaf",
            "formula": "score(p1) = max_p2 v(after p1 -> p2)",
            "trained_in_the_loop": True,
            "note": "These weights were FITTED with the search in the rollout, "
                    "so they must be RUN with the search. Running them greedily "
                    "measures a policy that was never trained.",
        }} if args.search else {}),
        # left False by save_weights; artifacts.py --parity run raises it
    })
    print(f"[cem] wrote {out}")
    print(f"[cem] FINAL held-out: mean lines {ev['mean_lines']:.1f} | "
          f"score/piece {ev['mean_norm_score_per_piece']:.1f} | "
          f"tetris rate {ev['mean_tetris_rate'] * 100:.1f}%")


if __name__ == "__main__":
    main()
