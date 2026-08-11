"""Afterstate DQN -- study plan (4), the far end of the axis.

WHERE THIS SITS ON THE STUDY-PLAN AXIS
--------------------------------------
The study plan's diagram gets from A2C to DQN by one move: "delete pi(a|s) and
replace a with argmax_a Q". The actor does not vanish; it becomes an actor with
zero parameters. Once the critic is fixed, the policy falls out of it for free.

    pi(s) = argmax over legal placements of Q(s, a)

That total subordination is what makes DQN off-policy, and the study plan is
precise about why. Look at the target:

    y = r + gamma * max_{a'} Q(s', a')

No behaviour policy appears anywhere in it. (s, a, r, s') is just a sample the
environment produced, and `max` does not care who chose a. So experience from an
old, worse policy is still valid training data -- which is what licenses the
replay buffer. Compare the policy-gradient theorem, where the state
distribution mu_pi and the value q_pi are both indexed by the CURRENT policy;
stale data there is simply wrong data. on/off-policy is forced by the equation,
not chosen by the engineer.

WHY AFTERSTATES MAKE THIS EASY
------------------------------
Ordinary DQN needs one output head per action and a fixed action set. Tetris has
neither: the number of legal placements changes every piece. Afterstates dissolve
the problem. Since the transition from a placement to its resulting board is
deterministic and fully known, the action value equals the value of the state it
lands in:

    Q(s, a) = r(s, a) + gamma * V(afterstate(s, a))

so a single scalar network V is enough, and

    max_{a'} Q(s', a')

is just an enumeration over the next piece's placements -- exactly the loop the
greedy policy already runs. One network, no action head, no fixed action space.

This file therefore learns V over the 8 afterstate features, the SAME input the
policy-gradient actors use. What differs is what the number means: in `pg.py` it
is a preference to be softmaxed, here it is an estimate of discounted future
return, and it is taken literally by an argmax.

THE TWO STABILISERS, AND WHAT THEY ARE FOR
------------------------------------------
* Replay buffer -- decorrelates consecutive transitions and reuses data.
  Legitimate only because the target is off-policy (see above).
* Target network -- the target r + gamma*V_target(s'') is computed from a frozen
  copy. Without it the target moves the instant the network does, and the
  regression chases itself. This is the same "critic errors become policy
  errors" failure the study plan describes for DDPG in week 3; the target
  network is the cheapest patch for it.

HONEST EXPECTATION
------------------
This will lose badly to CEM, and probably to 1-ply search. Eight features and a
32-unit hidden layer trained for ten minutes cannot match a direct search over
the same eight numbers evaluated on full games. The comparison is the deliverable.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np

from fastsim import (PLACEMENTS, TOTAL_ROWS, BagRandomizer, board_features_bits,
                     simulate, spawn_blocked)
from features import argmax_stable
from nn import MLP, Adam, clip_global_norm
from pg import (DEATH_PENALTY, FEATURE_SCALE, GAMMA, SURVIVAL_REWARD,
                N_ACTION_FEATURES)


def candidate_features(cols, top, piece):
    """Every legal placement's (normalised features, simulate-result, reward).

    Returns (X, results, rewards) with X of shape (n, 8), or (None, [], None)
    when the position is lost -- either the piece cannot spawn, or nothing fits.
    """
    if spawn_blocked(cols, piece):
        return None, [], None
    feats, results, rewards = [], [], []
    for rec in PLACEMENTS[piece]:
        res = simulate(cols, top, rec)
        if res is None:
            continue
        ncols, ntop, lines, lh, er = res
        rt, ct, ho, cw, agg, bump = board_features_bits(ncols)
        feats.append((lh, er, rt, ct, ho, cw, agg, bump))
        results.append(res)
        rewards.append(lines * lines + SURVIVAL_REWARD)
    if not results:
        return None, [], None
    X = np.array(feats, dtype=np.float64) / FEATURE_SCALE
    return X, results, np.array(rewards)


def collect_episode(job):
    """One epsilon-greedy episode. Returns transitions for the replay buffer.

    A transition is (phi, r, next_candidates, done):
        phi             (8,)      features of the afterstate actually entered
        r               scalar    reward collected on the way in
        next_candidates (m, 8)    every afterstate reachable from there, which
                                  is what `max_a' Q(s', a')` ranges over
        done            bool      True if the game ended there

    Storing the candidate set rather than the raw board is what keeps the target
    computation cheap: no re-simulation at training time.
    """
    net, seed, max_pieces, epsilon = job
    rng = np.random.default_rng(seed ^ 0xD90)

    cols = [0] * 10
    top = [TOTAL_ROWS] * 10
    bag = BagRandomizer(seed)
    current = bag.next_piece()

    transitions = []
    lines_total = 0
    pending = None       # transition waiting for its next-candidate set

    for _ in range(max_pieces):
        X, results, rewards = candidate_features(cols, top, current)

        if pending is not None:
            phi, r = pending
            if X is None:
                transitions.append((phi, r + DEATH_PENALTY,
                                    np.zeros((0, N_ACTION_FEATURES)), True))
            else:
                transitions.append((phi, r, X, False))
            pending = None

        if X is None:
            break

        if rng.random() < epsilon:
            idx = int(rng.integers(len(results)))
        else:
            idx = int(np.argmax(net(X)))

        pending = (X[idx].copy(), float(rewards[idx]))
        cols, top, lines, _lh, _er = results[idx]
        lines_total += lines
        current = bag.next_piece()

    if pending is not None:
        # Truncated by the piece cap: not terminal, so bootstrap continues.
        X, _res, _rw = candidate_features(cols, top, current)
        phi, r = pending
        if X is None:
            transitions.append((phi, r + DEATH_PENALTY,
                                np.zeros((0, N_ACTION_FEATURES)), True))
        else:
            transitions.append((phi, r, X, False))

    return transitions, lines_total, len(transitions)


def train(time_budget_s=600.0, hidden=32, lr=5e-4, batch_size=256,
          buffer_size=100_000, target_sync=250, updates_per_batch=24,
          episodes_per_round=32, max_pieces=300,
          eps_start=1.0, eps_end=0.05, eps_decay_rounds=40,
          clip=5.0, rng_seed=20260807, n_workers=24, verbose=True,
          train_seed_lo=1, train_seed_hi=100_000,
          ckpt_path=None, curve_path=None, resume=False):
    rng = np.random.default_rng(rng_seed)
    py_rng = random.Random(rng_seed)

    net = MLP([N_ACTION_FEATURES, hidden, 1], rng, out_scale=0.01)
    target = MLP([N_ACTION_FEATURES, hidden, 1], rng, out_scale=0.01)
    target.copy_from(net)
    opt = Adam(net.params(), lr=lr)

    buffer = deque(maxlen=buffer_size)
    history = []
    rnd = 0
    n_updates = 0

    # Resume restores the networks, the Adam moments and the update counter.
    # The REPLAY BUFFER is deliberately NOT saved -- it is 100k transitions of
    # numpy arrays, far larger than everything else combined, and it refills
    # within a few rounds. What resume must not lose is the optimiser state and
    # the target-sync phase, which are tiny.
    if resume and ckpt_path and os.path.exists(ckpt_path):
        with open(ckpt_path) as f:
            ck = json.load(f)
        from pg import _load_net
        _load_net(net, ck["net"])
        _load_net(target, ck["target"])
        opt.m = [np.array(x) for x in ck["opt_m"]]
        opt.v = [np.array(x) for x in ck["opt_v"]]
        opt.t = ck["opt_t"]
        rnd = ck["round"] + 1
        n_updates = ck["n_updates"]
        history = ck.get("history", [])
        rng = np.random.default_rng(rng_seed + rnd)
        if verbose:
            print(f"[dqn] resumed from {ckpt_path} at round {rnd} "
                  f"({n_updates} updates); replay buffer starts empty by design")

    t0 = time.perf_counter()

    pool = mp.Pool(n_workers)
    try:
        while time.perf_counter() - t0 < time_budget_s:
            eps = max(eps_end, eps_start - (eps_start - eps_end) * rnd / eps_decay_rounds)
            seeds = rng.integers(train_seed_lo, train_seed_hi,
                                 size=episodes_per_round).tolist()
            out = pool.map(collect_episode,
                           [(net, s, max_pieces, eps) for s in seeds])
            ep_lines = []
            for trans, lines, _n in out:
                buffer.extend(trans)
                ep_lines.append(lines)

            losses = []
            if len(buffer) >= batch_size:
                for _ in range(updates_per_batch):
                    batch = py_rng.sample(buffer, batch_size)

                    # ---- target: y = r + gamma * max_a' V_target(s'') ----
                    # The max is an enumeration over the next piece's
                    # placements. Terminal transitions have an empty candidate
                    # set and contribute y = r, with no bootstrap.
                    y = np.empty(batch_size)
                    phis = np.empty((batch_size, N_ACTION_FEATURES))
                    for i, (phi, r, nxt, done) in enumerate(batch):
                        phis[i] = phi
                        if done or len(nxt) == 0:
                            y[i] = r
                        else:
                            y[i] = r + GAMMA * float(target(nxt).max())

                    # ---- semi-gradient regression of V onto y ----
                    # y is held constant: no gradient flows into the target
                    # network. That is what makes this TD rather than true
                    # gradient descent on a well-defined objective.
                    pred, acts = net.forward(phis)
                    err = pred - y
                    grads_W, grads_b = net.backward(acts, err / batch_size)
                    g = grads_W + grads_b
                    clip_global_norm(g, clip)
                    opt.step(g)
                    losses.append(float((err * err).mean()))
                    n_updates += 1

                    if n_updates % target_sync == 0:
                        target.copy_from(net)

            rec = {
                "round": rnd,
                "epsilon": round(eps, 4),
                "buffer": len(buffer),
                "updates": n_updates,
                "mean_lines_behaviour": float(np.mean(ep_lines)),
                "max_lines_behaviour": int(np.max(ep_lines)),
                "td_loss": float(np.mean(losses)) if losses else float("nan"),
                "elapsed_s": round(time.perf_counter() - t0, 1),
            }
            history.append(rec)
            if ckpt_path:
                from artifacts import dump_json
                from pg import _net_state
                dump_json(ckpt_path, {
                    "round": rnd, "n_updates": n_updates, "history": history,
                    "net": _net_state(net), "target": _net_state(target),
                    "opt_m": [x.tolist() for x in opt.m],
                    "opt_v": [x.tolist() for x in opt.v], "opt_t": opt.t})
            if curve_path:
                os.makedirs(os.path.dirname(os.path.abspath(curve_path)),
                            exist_ok=True)
                with open(curve_path, "a") as cf:
                    cf.write(json.dumps({"run_id": f"dqn-e{episodes_per_round}"
                                                   f"-p{max_pieces}", **rec}) + "\n")
            if verbose and rnd % 5 == 0:
                print(f"[dqn] round {rnd:4d} | eps {eps:.3f} | buf {len(buffer):6d} "
                      f"| lines {rec['mean_lines_behaviour']:7.2f} "
                      f"(max {rec['max_lines_behaviour']:4d}) "
                      f"| td_loss {rec['td_loss']:8.4f} | {rec['elapsed_s']:6.1f}s")
            rnd += 1
    finally:
        pool.close()
        pool.join()

    return net, history


def greedy_rollout(job):
    net, seed, max_pieces = job
    cols = [0] * 10
    top = [TOTAL_ROWS] * 10
    bag = BagRandomizer(seed)
    current = bag.next_piece()
    lines_total = 0
    pieces = 0
    for _ in range(max_pieces):
        X, results, _rw = candidate_features(cols, top, current)
        if X is None:
            return lines_total, pieces, True
        idx = argmax_stable(net(X))
        cols, top, lines, _lh, _er = results[idx]
        lines_total += lines
        pieces += 1
        current = bag.next_piece()
    return lines_total, pieces, False


def evaluate(net, seeds, max_pieces=20_000, n_workers=10):
    with mp.Pool(n_workers) as pool:
        res = pool.map(greedy_rollout, [(net, s, max_pieces) for s in seeds])
    lines = [r[0] for r in res]
    return {
        "seeds": list(seeds),
        "lines": lines,
        "pieces": [r[1] for r in res],
        "mean_lines": float(np.mean(lines)),
        "min_lines": int(np.min(lines)),
        "max_lines": int(np.max(lines)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--time-budget", type=float, default=600.0)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--eval-cap", type=int, default=20_000)
    ap.add_argument("--max-pieces", type=int, default=300)
    ap.add_argument("--minutes", type=float, default=None,
                    help="wall-clock budget in minutes (overrides --time-budget)")
    ap.add_argument("--resume", action="store_true",
                    help="continue from weights/ckpt_dqn.json (networks + Adam "
                         "moments + update count; the replay buffer refills)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    wdir = os.path.join(here, "..", "weights")
    out = args.out or os.path.join(wdir, "dqn.json")
    ckpt = os.path.join(wdir, "ckpt_dqn.json")
    curve = os.path.join(wdir, "curve_dqn.jsonl")
    if args.minutes:
        args.time_budget = args.minutes * 60.0

    print(f"[dqn] training for {args.time_budget:.0f}s, workers {args.workers}")
    net, history = train(time_budget_s=args.time_budget,
                         n_workers=args.workers,
                         max_pieces=args.max_pieces,
                         ckpt_path=ckpt, curve_path=curve, resume=args.resume)

    holdout = list(range(900_001, 900_011))
    ev = evaluate(net, holdout, max_pieces=args.eval_cap)
    print(f"[dqn] held-out mean lines {ev['mean_lines']:.1f} "
          f"(min {ev['min_lines']}, max {ev['max_lines']})")

    from features import FEATURE_NAMES
    payload = {
        "name": "dqn",
        "kind": "mlp",
        "features": list(FEATURE_NAMES),
        "feature_scale": FEATURE_SCALE.tolist(),
        "layers": net.to_layers(),
        "activation": "relu",
        "meta": {
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "algorithm": "afterstate DQN (replay buffer + target network)",
            "study_plan_number": 4,
            "critic_role": "Q is the policy. There is no separate actor -- the "
                           "action is argmax over placements of the learned "
                           "afterstate value, so the policy has zero parameters "
                           "of its own and is 100% subordinate to the critic.",
            "off_policy": True,
            "off_policy_reason": "The target r + gamma*max_a' Q(s',a') contains no "
                                 "behaviour policy, so transitions from older "
                                 "policies remain valid. That is what makes the "
                                 "replay buffer legitimate here and illegitimate "
                                 "for the policy-gradient methods.",
            "afterstate_note": "Q(s,a) = r + gamma*V(afterstate), so one scalar "
                               "network suffices and max_a' is an enumeration "
                               "over the next piece's legal placements. No "
                               "fixed-size action head is needed.",
            "gamma": GAMMA,
            "reward": {"line_bonus": "lines_cleared^2",
                       "survival": SURVIVAL_REWARD, "death": DEATH_PENALTY},
            "rounds": len(history),
            # Always present, false until verified. `null` reads as "unknown
            # provenance"; artifacts.py --parity run raises this to true.
            "parity_verified": False,
            "history": history,
            "eval": ev,
            "train_seed_range": [1, 100000],
            "holdout_seed_range": [900001, 900010],
        },
    }
    from artifacts import dump_json
    dump_json(out, payload)
    print(f"[dqn] wrote {out}")


if __name__ == "__main__":
    main()
