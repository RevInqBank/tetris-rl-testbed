"""Minimal numpy MLP with hand-written backpropagation, plus Adam.

There is no PyTorch in this environment, so every gradient in `pg.py` and
`dqn.py` is computed here. The network is deliberately tiny (one hidden layer)
and the backward pass is written out explicitly rather than hidden behind an
autograd tape, because the point of this project is to be readable alongside
Sutton chapter 13.

SHAPES
------
`forward` is batched over ACTIONS, not over time: one call scores all legal
placements of one position at once.

    X : (n, d_in)          n candidate placements, d_in features each
    out : (n,)             one scalar per placement (a logit, or a value)

Weight matrices are stored as (d_in, d_out) so the JSON handed to the browser
can be consumed with a plain `h = h @ W + b` without transposing.

THE BACKWARD PASS
-----------------
`backward(cache, dout)` takes dL/d(out) for each of the n rows and returns the
parameter gradients, summed over rows. That summation is what makes the policy
gradient work in one call: the score-function gradient

    grad ln pi(a|s) = grad h(a) - sum_b pi(b) grad h(b)

is exactly `backward` with row coefficients

    dout[b] = 1{b == a} - pi(b)

so no per-action loop is needed. `pg.py` relies on this.
"""

from __future__ import annotations

import numpy as np


class MLP:
    """Fully connected net, ReLU hidden layers, linear scalar output."""

    def __init__(self, sizes, rng, out_scale=0.01):
        """sizes e.g. [8, 32, 1]. The final layer is initialised small so the
        policy starts close to uniform (logits ~ 0) and the value starts near 0,
        which keeps the first updates from being dominated by initialisation."""
        self.sizes = list(sizes)
        self.W, self.b = [], []
        for i in range(len(sizes) - 1):
            fan_in = sizes[i]
            last = (i == len(sizes) - 2)
            scale = out_scale if last else np.sqrt(2.0 / fan_in)   # He init
            self.W.append(rng.normal(0, scale, size=(sizes[i], sizes[i + 1])))
            self.b.append(np.zeros(sizes[i + 1]))

    # -- inference ---------------------------------------------------------

    def forward(self, X):
        """X (n, d_in) -> (out (n,), cache)."""
        acts = [X]
        h = X
        n_layers = len(self.W)
        for i in range(n_layers):
            z = h @ self.W[i] + self.b[i]
            if i < n_layers - 1:
                h = np.maximum(z, 0.0)          # ReLU
            else:
                h = z                            # linear head
            acts.append(h)
        return acts[-1][:, 0], acts

    def __call__(self, X):
        return self.forward(X)[0]

    # -- gradients ---------------------------------------------------------

    def backward(self, acts, dout):
        """dout (n,) = dL/d(out) per row. Returns (gW, gb) lists.

        Gradients are SUMMED over the n rows, not averaged -- callers scale
        explicitly so the learning-rate meaning stays obvious.
        """
        n_layers = len(self.W)
        gW = [None] * n_layers
        gb = [None] * n_layers
        delta = dout.reshape(-1, 1)              # (n, 1) at the linear head
        for i in range(n_layers - 1, -1, -1):
            a_in = acts[i]                       # input to layer i
            gW[i] = a_in.T @ delta
            gb[i] = delta.sum(axis=0)
            if i > 0:
                delta = (delta @ self.W[i].T) * (acts[i] > 0)   # ReLU derivative
        return gW, gb

    # -- serialisation -----------------------------------------------------

    def params(self):
        return self.W + self.b

    def to_layers(self):
        """The `layers` field of the weights JSON: W is (in, out)."""
        return [{"W": self.W[i].tolist(), "b": self.b[i].tolist()}
                for i in range(len(self.W))]

    def copy_from(self, other):
        for i in range(len(self.W)):
            self.W[i][...] = other.W[i]
            self.b[i][...] = other.b[i]


class Adam:
    """Adam over a flat list of parameter arrays (updated in place)."""

    def __init__(self, params, lr=1e-3, beta1=0.9, beta2=0.999, eps=1e-8):
        self.params = params
        self.lr, self.b1, self.b2, self.eps = lr, beta1, beta2, eps
        self.m = [np.zeros_like(p) for p in params]
        self.v = [np.zeros_like(p) for p in params]
        self.t = 0

    def step(self, grads, scale=1.0):
        """grads must line up with `params`. `scale` multiplies every gradient
        (used to turn a summed gradient into a mean over a batch)."""
        self.t += 1
        bc1 = 1 - self.b1 ** self.t
        bc2 = 1 - self.b2 ** self.t
        for i, (p, g) in enumerate(zip(self.params, grads)):
            g = g * scale
            self.m[i] = self.b1 * self.m[i] + (1 - self.b1) * g
            self.v[i] = self.b2 * self.v[i] + (1 - self.b2) * (g * g)
            p -= self.lr * (self.m[i] / bc1) / (np.sqrt(self.v[i] / bc2) + self.eps)


def clip_global_norm(grads, max_norm):
    """Rescale grads in place so their global L2 norm is at most max_norm.

    Policy-gradient updates on long Tetris episodes produce occasional enormous
    returns; without clipping a single lucky episode can destroy the policy.
    """
    total = np.sqrt(sum(float((g * g).sum()) for g in grads))
    if total > max_norm and total > 0:
        f = max_norm / total
        for g in grads:
            g *= f
    return total


def _self_test():
    """Finite-difference check of the hand-written backward pass."""
    rng = np.random.default_rng(0)
    net = MLP([5, 7, 1], rng, out_scale=0.5)
    X = rng.normal(size=(4, 5))
    dout = rng.normal(size=4)

    out, acts = net.forward(X)
    gW, gb = net.backward(acts, dout)

    def loss():
        return float(net.forward(X)[0] @ dout)

    eps = 1e-6
    worst = 0.0
    for arrs, grads in ((net.W, gW), (net.b, gb)):
        for a, g in zip(arrs, grads):
            it = np.nditer(a, flags=["multi_index"])
            while not it.finished:
                idx = it.multi_index
                old = a[idx]
                a[idx] = old + eps
                lp = loss()
                a[idx] = old - eps
                lm = loss()
                a[idx] = old
                num = (lp - lm) / (2 * eps)
                worst = max(worst, abs(num - g[idx]) / max(1.0, abs(num)))
                it.iternext()
    print(f"  max relative gradient error vs finite differences: {worst:.2e}")
    ok = worst < 1e-6
    print("PASS" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    _self_test()
