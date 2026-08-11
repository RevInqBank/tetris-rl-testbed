"""Policy-gradient family: REINFORCE, REINFORCE+baseline, and 1-step A2C.

    (1) reinforce            study plan (1)   no critic
    (2) reinforce_baseline   study plan (2)   critic as a variance-reduction baseline
    (3) a2c                  study plan (3)   critic as a bootstrap target

THIS FILE EXISTS TO MAKE ONE LINE VISIBLE
-----------------------------------------
The study plan's whole thesis is that (2) and (3) are not different families of
algorithm. They are the same update with one substitution:

    REINFORCE with baseline (Sutton 13.4)
        theta <- theta + alpha ( G_t          - v(S_t) ) grad ln pi(A_t|S_t)
                            \_ actual return _/

    1-step actor-critic (Sutton 13.5)
        theta <- theta + alpha ( R + gamma v(S_{t+1}) - v(S_t) ) grad ln pi(A_t|S_t)
                            \_ return replaced by an estimate _/

Search this file for THE ONE LINE. That `if` is the entire difference, and
everything downstream -- the bias, the on-policy requirement, whether Sutton is
willing to call the thing an actor-critic -- follows from it.

WHY (2) IS NOT AN ACTOR-CRITIC
------------------------------
Subtracting v(S_t) is free. For any b(s) that does not depend on the action,

    sum_a b(s) grad pi(a|s) = b(s) grad sum_a pi(a|s) = b(s) grad 1 = 0

so the expected update direction is unchanged no matter how wrong v is. The
baseline only shrinks the variance. It never changes where the update points, so
it is not judging anything -- it does not earn the name critic.

The bootstrap target is different in kind. v(S_{t+1}) is an estimate of a state
OTHER than the one being updated, and it sits inside the target. If it is wrong,
the target is wrong, and the update direction itself is wrong. That is bias, and
that is a critic actually rendering a verdict. What is bought in exchange: no
waiting for the episode to end, so learning can happen every step.

Hence the study plan's line -- the classification is not "is there a v?" but
"does v participate in the bootstrap?"

THE POLICY
----------
Actions are placements, and each placement is described by the 8 afterstate
features. So the policy is Sutton's linear/nonlinear softmax in action
preferences (13.2):

    h(s, a) = net_theta( f(afterstate(s, a)) )
    pi(a|s) = softmax over the legal placements of h(s, a)

This is why the score-function gradient collapses into a single backward pass:

    grad ln pi(a|s) = grad h(a) - sum_b pi(b) grad h(b)

which is `nn.MLP.backward` with row coefficients `onehot(a) - pi`. See nn.py.

THE CRITIC'S INPUT IS NOT THE SAME VECTOR AS THE ACTOR'S
--------------------------------------------------------
The actor scores (state, action) pairs, so it eats afterstate features. The
critic estimates v(s) for a state with no action chosen yet, and two of the
eight features (landing_height, eroded_piece_cells) are properties of a
placement and simply do not exist for a bare state. So the critic gets the six
board-only features plus a 7-way one-hot of the current piece -- 13 numbers.
Holding an I-piece is genuinely worth more than holding an S-piece, and the
critic cannot express that without knowing which piece is in hand.

HONESTY NOTES (these matter for reading the results)
----------------------------------------------------
* No advantage normalisation anywhere. Standardising advantages would quietly
  insert a baseline into plain REINFORCE and destroy the comparison this file
  exists to make. REINFORCE is therefore as unstable here as the theory says.
* All three share the identical reward function, feature normalisation, network
  shape, optimiser, entropy bonus, batch size, and wall-clock budget. The only
  differences are THE ONE LINE and whether a critic exists at all.
* These will not approach CEM. That is the expected and interesting result, not
  a bug to be tuned away.
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
                     simulate, spawn_blocked)
from features import argmax_stable
from nn import MLP, Adam, clip_global_norm

# ---------------------------------------------------------------------------
# Feature normalisation
# ---------------------------------------------------------------------------
# Raw features span very different ranges (aggregate_height reaches ~160 while
# eroded_piece_cells is usually 0), and an unnormalised input makes the first
# hidden layer effectively see only the largest feature. Divisors are the 99th
# percentile measured over random play, rounded; they are constants, not learned,
# so the JSON handed to the browser stays self-contained -- BUT the browser must
# apply them too. Communicated to `web` alongside the schema.
FEATURE_SCALE = np.array([20.0,   # landing_height
                          4.0,    # eroded_piece_cells
                          110.0,  # row_transitions
                          60.0,   # column_transitions
                          75.0,   # holes
                          75.0,   # cumulative_wells
                          160.0,  # aggregate_height
                          40.0],  # bumpiness
                         dtype=np.float64)
STATE_SCALE = FEATURE_SCALE[2:]     # the six board-only features

N_ACTION_FEATURES = 8
N_STATE_FEATURES = 6 + 7            # board features + one-hot current piece

# ---------------------------------------------------------------------------
# Reward
# ---------------------------------------------------------------------------
# Squared line bonus: a tetris (4 lines) is worth 16, four singles are worth 4.
# Without the convexity the agent has no reason to ever build depth.
# The survival term is deliberately tiny -- large enough to prefer living, small
# enough that stacking flat forever is not competitive with clearing.
# There is no per-placement reward: paying for placing a piece at all is the
# classic way to get a policy that survives and never scores.
SURVIVAL_REWARD = 0.01
DEATH_PENALTY = -1.0
GAMMA = 0.99


def state_features(cols, piece: int) -> np.ndarray:
    rt, ct, ho, cw, agg, bump = board_features_bits(cols)
    v = np.empty(N_STATE_FEATURES)
    v[:6] = np.array([rt, ct, ho, cw, agg, bump]) / STATE_SCALE
    v[6:] = 0.0
    v[6 + piece] = 1.0
    return v


def softmax(z):
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


# ---------------------------------------------------------------------------
# Episode collection (runs in worker processes)
# ---------------------------------------------------------------------------

def collect_episode(job):
    """Play one episode under the current actor. Returns raw arrays only.

    The worker does not compute gradients -- it returns the action-feature
    matrices and the sampled index for each step, and the master recomputes the
    forward pass. Keeping all parameter updates in one process removes any
    chance of workers drifting onto stale parameters mid-batch.
    """
    actor, seed, max_pieces, greedy = job

    cols = [0] * 10
    top = [TOTAL_ROWS] * 10
    bag = BagRandomizer(seed)
    current = bag.next_piece()

    act_feats, chosen, rewards, st_feats = [], [], [], []
    lines_total = 0
    died = False
    rng = np.random.default_rng(seed ^ 0x5EED)

    for _ in range(max_pieces):
        if spawn_blocked(cols, current):
            died = True
            break

        recs = PLACEMENTS[current]
        feats, results = [], []
        for rec in recs:
            res = simulate(cols, top, rec)
            if res is None:
                continue
            ncols, ntop, lines, lh, er = res
            rt, ct, ho, cw, agg, bump = board_features_bits(ncols)
            feats.append((lh, er, rt, ct, ho, cw, agg, bump))
            results.append(res)
        if not results:
            died = True
            break

        X = np.array(feats, dtype=np.float64) / FEATURE_SCALE
        logits = actor(X)
        p = softmax(logits)
        idx = argmax_stable(p) if greedy else int(rng.choice(len(p), p=p))

        st_feats.append(state_features(cols, current))
        act_feats.append(X.astype(np.float32))
        chosen.append(idx)

        cols, top, lines, _lh, _er = results[idx]
        lines_total += lines
        rewards.append(lines * lines + SURVIVAL_REWARD)
        current = bag.next_piece()

    if died:
        if rewards:
            rewards[-1] += DEATH_PENALTY
        terminal_state = None
    else:
        # Hit the piece cap: the episode is truncated, not terminated, so the
        # bootstrap must continue through v(S_T) rather than assume 0.
        terminal_state = state_features(cols, current)

    return {
        "act_feats": act_feats,
        "chosen": chosen,
        "rewards": np.array(rewards, dtype=np.float64),
        "state_feats": (np.array(st_feats, dtype=np.float64)
                        if st_feats else np.zeros((0, N_STATE_FEATURES))),
        "terminal_state": terminal_state,
        "lines": lines_total,
        "pieces": len(chosen),
        "died": died,
    }


# ---------------------------------------------------------------------------
# The update
# ---------------------------------------------------------------------------

def compute_deltas(ep, critic, mode: str):
    """Return (delta, critic_target, values) for one episode.

    `delta` is the multiplier on grad ln pi -- Sutton's (G_t - v), or the TD
    error, or the bare return. `critic_target` is what the value function is
    regressed onto (None when there is no critic).
    """
    r = ep["rewards"]
    T = len(r)
    if T == 0:
        return None, None, None

    # Discounted returns G_t, computed backwards. When the episode was
    # truncated at the piece cap rather than ending, the tail is bootstrapped so
    # G_t is not silently biased towards zero.
    G = np.empty(T)
    if ep["terminal_state"] is not None and critic is not None:
        running = float(critic(ep["terminal_state"][None, :])[0])
    else:
        running = 0.0
    for t in range(T - 1, -1, -1):
        running = r[t] + GAMMA * running
        G[t] = running

    if mode == "reinforce":
        # (1) No critic at all. The full return is the multiplier.
        return G, None, None

    S = ep["state_feats"]
    v = critic(S)                                     # v(S_0) .. v(S_{T-1})

    # v(S_{t+1}) for every t: shift v by one and append the terminal value.
    v_next = np.empty(T)
    v_next[:-1] = v[1:]
    if ep["terminal_state"] is not None:
        v_next[-1] = float(critic(ep["terminal_state"][None, :])[0])
    else:
        v_next[-1] = 0.0                              # true terminal state

    # ================== THE ONE LINE ==================================
    # This is the whole difference between study-plan (2) and (3).
    # (2) regresses on, and compares against, the ACTUAL return G_t.
    # (3) replaces that return with a one-step ESTIMATE R + gamma*v(S_{t+1}).
    # Everything else in this function, and in the training loop, is identical.
    target = (r + GAMMA * v_next) if mode == "a2c" else G
    # ==================================================================

    delta = target - v          # actor multiplier AND critic error, one quantity
    return delta, target, v


def update_batch(actor, critic, opt_a, opt_c, batch, mode, entropy_coef, clip):
    """One gradient step from a batch of episodes."""
    gW_a = [np.zeros_like(w) for w in actor.W]
    gb_a = [np.zeros_like(b) for b in actor.b]
    if critic is not None:
        gW_c = [np.zeros_like(w) for w in critic.W]
        gb_c = [np.zeros_like(b) for b in critic.b]

    n_steps = 0
    ent_sum = 0.0
    critic_sq_err = 0.0

    for ep in batch:
        delta, target, v = compute_deltas(ep, critic, mode)
        if delta is None:
            continue
        T = len(delta)
        n_steps += T

        # ---- actor ----
        for t in range(T):
            X = ep["act_feats"][t].astype(np.float64)
            logits, acts = actor.forward(X)
            p = softmax(logits)
            a = ep["chosen"][t]

            # d/dh of  -delta * ln pi(a)   is   -delta * (onehot(a) - p)
            coef = -delta[t] * (-p)
            coef[a] += -delta[t] * 1.0

            # Entropy bonus, maximised: d/dh of -H = p * (log p + H)
            if entropy_coef:
                logp = np.log(np.maximum(p, 1e-12))
                H = -(p * logp).sum()
                ent_sum += H
                coef += entropy_coef * p * (logp + H)

            dW, db = actor.backward(acts, coef)
            for i in range(len(gW_a)):
                gW_a[i] += dW[i]
                gb_a[i] += db[i]

        # ---- critic ----
        # Loss 0.5*(target - v)^2, so d/dv = -(target - v) = -delta.
        # The target is treated as a constant: no gradient flows through
        # v(S_{t+1}) in the A2C case. This is the usual semi-gradient TD of
        # Sutton 9.3, and it is why TD is not true gradient descent.
        if critic is not None:
            S = ep["state_feats"]
            _vv, cacts = critic.forward(S)
            dW, db = critic.backward(cacts, -delta)
            for i in range(len(gW_c)):
                gW_c[i] += dW[i]
                gb_c[i] += db[i]
            critic_sq_err += float((delta * delta).sum())

    if n_steps == 0:
        return {"n_steps": 0}

    scale = 1.0 / n_steps
    ga = gW_a + gb_a
    for g in ga:
        g *= scale
    gnorm = clip_global_norm(ga, clip)
    opt_a.step(ga, scale=1.0)

    if critic is not None:
        gc = gW_c + gb_c
        for g in gc:
            g *= scale
        clip_global_norm(gc, clip)
        opt_c.step(gc, scale=1.0)

    return {
        "n_steps": n_steps,
        "entropy": ent_sum / n_steps if entropy_coef else float("nan"),
        "actor_grad_norm": gnorm,
        "critic_mse": critic_sq_err / n_steps if critic is not None else float("nan"),
    }


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

MODES = ("reinforce", "reinforce_baseline", "a2c")


def _net_state(net):
    return {"W": [w.tolist() for w in net.W], "b": [b.tolist() for b in net.b]}


def _load_net(net, st):
    for i, w in enumerate(st["W"]):
        net.W[i][...] = np.array(w)
    for i, b in enumerate(st["b"]):
        net.b[i][...] = np.array(b)


def train(mode: str, time_budget_s=600.0, batch_episodes=48, max_pieces=300,
          lr_actor=3e-4, lr_critic=1e-3, entropy_coef=0.01, clip=5.0,
          hidden=32, rng_seed=20260807, n_workers=None, verbose=True,
          train_seed_lo=1, train_seed_hi=100_000,
          ckpt_path=None, curve_path=None, resume=False):
    assert mode in MODES
    rng = np.random.default_rng(rng_seed)
    if n_workers is None:
        n_workers = min(batch_episodes, os.cpu_count() or 8)

    actor = MLP([N_ACTION_FEATURES, hidden, 1], rng, out_scale=0.01)
    opt_a = Adam(actor.params(), lr=lr_actor)

    # (1) REINFORCE has no critic. Not "a critic set to zero" -- no critic.
    if mode == "reinforce":
        critic, opt_c = None, None
    else:
        critic = MLP([N_STATE_FEATURES, hidden, 1], rng, out_scale=0.01)
        opt_c = Adam(critic.params(), lr=lr_critic)

    history = []
    it = 0

    # Resume. Adam's moment estimates are part of the state: dropping them
    # restarts the optimiser cold and the first post-resume steps are much
    # larger than they should be, which shows up as a visible dip in the curve.
    if resume and ckpt_path and os.path.exists(ckpt_path):
        with open(ckpt_path) as f:
            ck = json.load(f)
        _load_net(actor, ck["actor"])
        opt_a.m = [np.array(x) for x in ck["opt_a_m"]]
        opt_a.v = [np.array(x) for x in ck["opt_a_v"]]
        opt_a.t = ck["opt_a_t"]
        if critic is not None and ck.get("critic"):
            _load_net(critic, ck["critic"])
            opt_c.m = [np.array(x) for x in ck["opt_c_m"]]
            opt_c.v = [np.array(x) for x in ck["opt_c_v"]]
            opt_c.t = ck["opt_c_t"]
        it = ck["iteration"] + 1
        history = ck.get("history", [])
        rng = np.random.default_rng(rng_seed + it)
        if verbose:
            print(f"[{mode}] resumed from {ckpt_path} at iteration {it}")

    t0 = time.perf_counter()
    pool = mp.Pool(n_workers)
    try:
        while time.perf_counter() - t0 < time_budget_s:
            seeds = rng.integers(train_seed_lo, train_seed_hi,
                                 size=batch_episodes).tolist()
            batch = pool.map(collect_episode,
                             [(actor, s, max_pieces, False) for s in seeds])
            stats = update_batch(actor, critic, opt_a, opt_c, batch, mode,
                                 entropy_coef, clip)

            lines = [b["lines"] for b in batch]
            pieces = [b["pieces"] for b in batch]
            rec = {
                "iteration": it,
                "episodes_so_far": (it + 1) * batch_episodes,
                "mean_lines": float(np.mean(lines)),
                "max_lines": int(np.max(lines)),
                "mean_pieces": float(np.mean(pieces)),
                "entropy": stats.get("entropy"),
                "critic_mse": stats.get("critic_mse"),
                "elapsed_s": round(time.perf_counter() - t0, 1),
            }
            history.append(rec)
            if ckpt_path:
                from artifacts import dump_json
                ck = {"mode": mode, "iteration": it, "history": history,
                      "actor": _net_state(actor),
                      "opt_a_m": [x.tolist() for x in opt_a.m],
                      "opt_a_v": [x.tolist() for x in opt_a.v],
                      "opt_a_t": opt_a.t}
                if critic is not None:
                    ck.update({"critic": _net_state(critic),
                               "opt_c_m": [x.tolist() for x in opt_c.m],
                               "opt_c_v": [x.tolist() for x in opt_c.v],
                               "opt_c_t": opt_c.t})
                dump_json(ckpt_path, ck)
            if curve_path:
                os.makedirs(os.path.dirname(os.path.abspath(curve_path)),
                            exist_ok=True)
                with open(curve_path, "a") as cf:
                    cf.write(json.dumps({"run_id": f"{mode}-b{batch_episodes}"
                                                   f"-p{max_pieces}", **rec}) + "\n")
            if verbose and it % 5 == 0:
                print(f"[{mode}] it {it:4d} | lines {rec['mean_lines']:7.2f} "
                      f"(max {rec['max_lines']:4d}) | pieces {rec['mean_pieces']:6.1f} "
                      f"| H {rec['entropy']:.3f} "
                      f"| critic_mse {rec['critic_mse']:9.3f} "
                      f"| {rec['elapsed_s']:6.1f}s")
            it += 1
    finally:
        pool.close()
        pool.join()

    return actor, critic, history


def evaluate(actor, seeds, max_pieces=20_000, n_workers=None, greedy=True):
    if n_workers is None:
        n_workers = min(len(seeds), os.cpu_count() or 8)
    with mp.Pool(n_workers) as pool:
        eps = pool.map(collect_episode,
                       [(actor, s, max_pieces, greedy) for s in seeds])
    lines = [e["lines"] for e in eps]
    return {
        "seeds": list(seeds),
        "lines": lines,
        "pieces": [e["pieces"] for e in eps],
        "mean_lines": float(np.mean(lines)),
        "min_lines": int(np.min(lines)),
        "max_lines": int(np.max(lines)),
        "greedy": greedy,
    }


CRITIC_ROLE = {
    "reinforce": "None. The multiplier on grad ln pi is the actual return G_t. "
                 "Unbiased, and very high variance.",
    "reinforce_baseline": "Variance reduction only. v(S_t) is subtracted from "
                          "G_t, which leaves the expected update direction "
                          "untouched (sum_a b(s) grad pi(a|s) = 0). It does not "
                          "bootstrap, so Sutton does not call this an "
                          "actor-critic.",
    "a2c": "Bootstrap target. G_t is replaced by R + gamma v(S_{t+1}), so an "
           "error in v becomes an error in the target and therefore bias in the "
           "update direction. In exchange, learning is online -- no waiting for "
           "the episode to end.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, required=True)
    ap.add_argument("--time-budget", type=float, default=600.0)
    ap.add_argument("--batch", type=int, default=48)
    ap.add_argument("--max-pieces", type=int, default=300)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--eval-cap", type=int, default=20_000)
    ap.add_argument("--minutes", type=float, default=None,
                    help="wall-clock budget in minutes (overrides --time-budget)")
    ap.add_argument("--resume", action="store_true",
                    help="continue from weights/ckpt_<mode>.json, restoring the "
                         "Adam moments too")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    wdir = os.path.join(here, "..", "weights")
    out = args.out or os.path.join(wdir, f"{args.mode}.json")
    ckpt = os.path.join(wdir, f"ckpt_{args.mode}.json")
    curve = os.path.join(wdir, f"curve_{args.mode}.jsonl")
    if args.minutes:
        args.time_budget = args.minutes * 60.0

    print(f"[{args.mode}] training for {args.time_budget:.0f}s, "
          f"batch {args.batch} episodes, workers {args.workers}")
    actor, critic, history = train(args.mode, time_budget_s=args.time_budget,
                                   batch_episodes=args.batch,
                                   max_pieces=args.max_pieces,
                                   n_workers=args.workers,
                                   ckpt_path=ckpt, curve_path=curve,
                                   resume=args.resume)

    holdout = list(range(900_001, 900_011))
    ev = evaluate(actor, holdout, max_pieces=args.eval_cap, greedy=True)
    ev_sample = evaluate(actor, holdout, max_pieces=args.eval_cap, greedy=False)
    print(f"[{args.mode}] held-out greedy  mean lines {ev['mean_lines']:.1f}")
    print(f"[{args.mode}] held-out sampled mean lines {ev_sample['mean_lines']:.1f}")

    from features import FEATURE_NAMES
    payload = {
        "name": args.mode,
        "kind": "policy_mlp",
        "features": list(FEATURE_NAMES),
        "feature_scale": FEATURE_SCALE.tolist(),
        "layers": actor.to_layers(),
        "activation": "relu",
        "meta": {
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "algorithm": args.mode,
            "critic_role": CRITIC_ROLE[args.mode],
            "study_plan_number": {"reinforce": 1, "reinforce_baseline": 2,
                                  "a2c": 3}[args.mode],
            "bootstraps": args.mode == "a2c",
            "has_critic": args.mode != "reinforce",
            "gamma": GAMMA,
            "reward": {"line_bonus": "lines_cleared^2",
                       "survival": SURVIVAL_REWARD,
                       "death": DEATH_PENALTY},
            "episodes": len(history) * args.batch,
            "train_max_pieces": args.max_pieces,
            "no_advantage_normalisation": True,
            # Always present, false until verified. `null` reads as "unknown
            # provenance"; artifacts.py --parity run raises this to true.
            "parity_verified": False,
            "history": history,
            "eval": ev,
            "eval_sampled": ev_sample,
            "train_seed_range": [1, 100000],
            "holdout_seed_range": [900001, 900010],
        },
    }
    if critic is not None:
        payload["meta"]["critic_layers_note"] = (
            "The critic is not shipped: it exists only to shape the actor's "
            "update and is never consulted at play time. Its input is the 6 "
            "board features plus a 7-way piece one-hot, not the 8 action features.")
    from artifacts import dump_json
    dump_json(out, payload)
    print(f"[{args.mode}] wrote {out}")


if __name__ == "__main__":
    main()
