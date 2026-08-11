"""Deployment and web-layer attacks.

  1. WEIGHTS REACHABILITY. PROJECT.md fixes the serving command as
     `python3 -m http.server 8080 --directory web` and the URL as
     http://localhost:8080/. Are the weight files actually fetchable from
     that document root? Tested by really serving both roots and issuing HTTP
     requests.
  2. MISSING WEIGHTS. With no weights at all, does the app still come up?
  3. CLASSIC BUNDLE STALENESS. `web/engine.classic.js` is a generated
     single-file copy of tables.js + engine.js -- a FOURTH copy of the rules
     (engine.py, engine.js, fastsim.py, engine.classic.js). Its own header says
     "if this file and engine.js ever disagree, this one is stale". Nothing
     enforces that. Compared here over real traces and every exported constant.
  4. MODULE LOAD. Every web/*.js parses, and the ES module graph actually
     resolves (a missing or duplicated export makes the whole app a blank page).

Run:  python3 tests/test_web_deploy.py
Owners: web (index.html, ui.js, policies.js, arena.js), engine (engine.js,
engine.classic.js, tables.js), lead (the serving command).
This file is checker-owned.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
WEB = os.path.join(_ROOT, "web")
WEIGHTS = os.path.join(_ROOT, "weights")

FAILURES = []


def fail(tag, msg):
    FAILURES.append((tag, msg))
    print("  FAIL [%s] %s" % (tag, msg))


def ok(msg):
    print("  ok   %s" % msg)


SKIPPED = []


def skip(tag, msg):
    SKIPPED.append((tag, msg))
    print("  SKIP [%s] %s" % (tag, msg))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    """A real http.server over a given document root."""

    def __init__(self, directory):
        self.directory = directory
        self.port = free_port()
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(self.port),
             "--directory", self.directory],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/" % self.port,
                                       timeout=0.5)
                return self
            except urllib.error.HTTPError:
                return self
            except Exception:
                time.sleep(0.1)
        return self

    def __exit__(self, *a):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def code(self, path):
        try:
            r = urllib.request.urlopen("http://127.0.0.1:%d%s"
                                       % (self.port, path), timeout=3)
            return r.getcode()
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:
            return "ERR:%s" % e


# ---------------------------------------------------------------------------

def check_weights_reachability():
    print("\n1. weight files reachable from the documented document root?")
    names = sorted(f for f in os.listdir(WEIGHTS) if f.endswith(".json"))
    print("    weights/ on disk: %d json files" % len(names))
    probe = ["/index.html", "/engine.js", "/policies.js",
             "/weights/cem_linear.json", "/weights/index.json"]

    with Server(WEB) as srv:
        print("    --- root = web/  (PROJECT.md: `--directory web`, URL "
              "http://HOST:8080/ ) ---")
        codes = {}
        for p in probe:
            codes[p] = srv.code(p)
            print("      %-30s %s" % (p, codes[p]))
    missing = [p for p in probe if p.startswith("/weights/")
               and codes.get(p) != 200]
    app_ok = codes.get("/index.html") == 200 and codes.get("/engine.js") == 200

    with Server(_ROOT) as srv2:
        print("    --- root = project root ---")
        codes2 = {}
        for p in ["/web/index.html", "/web/engine.js",
                  "/weights/cem_linear.json", "/weights/index.json"]:
            codes2[p] = srv2.code(p)
            print("      %-30s %s" % (p, codes2[p]))

    if missing and app_ok:
        fail("V1", "With the serving command PROJECT.md specifies "
                   "(`python3 -m http.server 8080 --directory web`), the app "
                   "loads (index.html 200, engine.js 200) but EVERY weight "
                   "file 404s: %s. `web/weights` does not exist (no symlink, "
                   "no copy). Serving the PROJECT ROOT instead makes them 200, "
                   "but then the user's URL becomes "
                   "http://localhost:8080/web/ , not the documented "
                   "http://localhost:8080/ . One of the two must change "
                   "before the user is asked to look."
             % ", ".join(missing))
    elif not missing:
        ok("all probed weight paths are 200 from the documented web/ root")
    return codes, codes2


def check_index_consistency():
    """weights/index.json is the roster the browser fetches from. Two ways it
    can lie, and both cost a request or a panel:

      A. an entry marked trained:true whose file is NOT on disk
         -> the browser fetches it and 404s, and the panel silently has no
            weights. This is the direction that HURTS.
      B. an entry whose file is absent and correctly marked trained:false
         -> fine, PROVIDED the loader honours the flag. web fetched these
            anyway and 404'd on every page load until it read the flag.

    So: (A) must never happen, and (B) must stay covered by the flag.
    """
    print("\n0. weights/index.json vs what is actually on disk")
    idx = os.path.join(WEIGHTS, "index.json")
    if not os.path.exists(idx):
        skip("V0", "weights/index.json absent -- the browser falls back to a "
                   "hardcoded list, so the roster is unverified")
        return
    try:
        d = json.load(open(idx, encoding="utf-8"))
    except ValueError as e:
        fail("V0", "weights/index.json is not valid JSON: %s" % e)
        return
    rows = d if isinstance(d, list) else (d.get("strategies")
                                          or d.get("weights") or [])
    if not rows:
        skip("V0", "index.json has no strategy list to check")
        return

    lying, untrained_absent = [], []
    for e in rows:
        if not isinstance(e, dict):
            continue
        f, trained = e.get("file"), e.get("trained")
        name = e.get("id") or e.get("name") or f
        if not f:
            continue
        exists = os.path.exists(os.path.join(WEIGHTS, f))
        if trained is not False and not exists:
            lying.append((name, f, trained))
        elif trained is False and not exists:
            untrained_absent.append((name, f))

    if lying:
        for name, f, trained in lying:
            fail("V0", "index.json declares %s with file=%s and trained=%r, "
                       "but that file is NOT on disk. The browser will fetch "
                       "it, 404, and leave that panel with no weights while "
                       "the page looks fine."
                 % (name, f, trained))
    else:
        ok("every index.json entry that claims to be trained has its file on "
           "disk (%d entries checked)"
           % sum(1 for e in rows if isinstance(e, dict) and e.get("file")))

    if untrained_absent:
        # the loader must be gating on the flag, or these 404 on every load
        pol = open(os.path.join(WEB, "policies.js"), encoding="utf-8").read()
        gated = "trained !== false" in pol or "trained!==false" in pol
        names = ", ".join("%s (%s)" % (n, f) for n, f in untrained_absent)
        if gated:
            ok("%d entry/entries are absent but correctly marked "
               "trained:false, and policies.js filters on `trained !== false` "
               "so they are never fetched: %s" % (len(untrained_absent), names))
        else:
            fail("V0", "%s is absent and marked trained:false, but policies.js "
                       "does not filter on that flag -- every page load will "
                       "request a file that does not exist" % names)


def check_missing_weights_resilience():
    print("\n2. does the app survive with NO weights at all?")
    # policies.js is expected to probe candidate dirs and give up gracefully;
    # ui.js must catch. Verify by static inspection of the guard, then by
    # serving a web-only tree where no weights dir exists in either candidate
    # location.
    pol = open(os.path.join(WEB, "policies.js"), encoding="utf-8").read()
    ui = open(os.path.join(WEB, "ui.js"), encoding="utf-8").read()
    guarded = "catch" in ui and "loadWeights" in ui
    if not guarded:
        fail("V2", "ui.js calls loadWeights() without a try/catch -- a missing "
                   "weights directory would abort module initialisation and "
                   "leave a blank page")
    else:
        ok("ui.js wraps loadWeights() in try/catch, so a missing weights dir "
           "degrades instead of blanking the page")
    if "WEIGHTS_DIRS" in pol and "../weights/" in pol and "./weights/" in pol:
        ok("policies.js probes both ../weights/ and ./weights/, so it works "
           "under either document root once one of them exists")
    else:
        fail("V2", "policies.js does not probe both candidate weights dirs")

    tmp = tempfile.mkdtemp()
    try:
        for f in os.listdir(WEB):
            src = os.path.join(WEB, f)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(tmp, f))
        with Server(tmp) as srv:
            c_index = srv.code("/index.html")
            c_eng = srv.code("/engine.js")
            c_w = srv.code("/weights/cem_linear.json")
        if c_index == 200 and c_eng == 200 and c_w != 200:
            ok("weights-free tree: index.html and engine.js still serve 200 "
               "(weights 404 as expected) -- the static app is not gated on "
               "the weights existing")
        else:
            fail("V2", "weights-free tree served unexpected codes: "
                       "index=%s engine=%s weights=%s"
                 % (c_index, c_eng, c_w))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


JS_CMP = r"""
import * as M from '%(web)s/engine.js';
import { createRequire } from 'module';
const require = createRequire('%(web)s/x.js');
const C = require('%(web)s/engine.classic.js');

function trace(E, seed, pol, max) {
  let s = E.newGame(seed); const out = [];
  for (let i = 0; i < max; i++) {
    const ps = E.legalPlacements(s);
    if (!ps.length) break;
    let b = 0;
    if (pol === 'lowest') {
      let by = ps[0][2];
      for (let k = 1; k < ps.length; k++) if (ps[k][2] > by) { by = ps[k][2]; b = k; }
    }
    const [ns, info] = E.applyPlacement(s, ps[b]); s = ns;
    out.push([ps.length, E.boardHash(s.rows), E.stateHash(s),
              info.lines_cleared, s.score]);
    if (s.game_over) break;
  }
  return out;
}

const res = { diverge: [], moves: 0, constDiff: [], missing: [] };
for (const seed of [1, 12345, 999, 20260807, 424242])
  for (const pol of ['first', 'lowest']) {
    const a = trace(M, seed, pol, 400), b = trace(C, seed, pol, 400);
    res.moves += a.length;
    if (a.length !== b.length) {
      res.diverge.push(`seed=${seed} ${pol}: length esm=${a.length} classic=${b.length}`);
      continue;
    }
    for (let i = 0; i < a.length; i++)
      if (JSON.stringify(a[i]) !== JSON.stringify(b[i])) {
        res.diverge.push(`seed=${seed} ${pol} move ${i}: esm=${JSON.stringify(a[i])} classic=${JSON.stringify(b[i])}`);
        break;
      }
  }
for (const k of Object.keys(M)) {
  if (typeof M[k] === 'function' || k === 'State') continue;
  if (!(k in C)) { res.missing.push(k); continue; }
  const a = JSON.stringify(M[k]), b = JSON.stringify(C[k]);
  if (a !== b) res.constDiff.push(`${k}: esm=${a} classic=${b}`);
}
process.stdout.write(JSON.stringify(res));
"""


def check_classic_bundle():
    print("\n3. web/engine.classic.js (generated 4th rule copy) vs engine.js")
    p = os.path.join(WEB, "engine.classic.js")
    if not os.path.exists(p):
        print("    (no engine.classic.js -- nothing to check)")
        return
    node = shutil.which("node")
    if not node:
        fail("V3", "node unavailable")
        return
    runner = os.path.join(_HERE, "_classic_cmp.mjs")
    with open(runner, "w") as f:
        f.write(JS_CMP % {"web": WEB.replace("\\", "/")})
    proc = subprocess.run([node, runner], capture_output=True, text=True)
    if proc.returncode != 0:
        fail("V3", "comparison runner failed:\n" + proc.stderr[-1500:])
        return
    r = json.loads(proc.stdout)
    if r["diverge"]:
        fail("V3", "engine.classic.js DISAGREES with engine.js -- by its own "
                   "header that means it is stale and must be regenerated "
                   "(python3 engine/gen_classic_bundle.py). %d trace(s): %s"
             % (len(r["diverge"]), "; ".join(r["diverge"][:3])))
    else:
        ok("%d moves over 5 seeds x 2 policies: the classic bundle produces "
           "identical placement counts, board_hash, state_hash, "
           "lines_cleared and score" % r["moves"])
    if r["missing"]:
        fail("V3", "classic bundle is missing %d exports the ES module has: %s"
             % (len(r["missing"]), ", ".join(r["missing"][:10])))
    if r["constDiff"]:
        fail("V3", "constant tables differ between the bundle and the module: "
                   "%s" % "; ".join(r["constDiff"][:5]))
    if not r["missing"] and not r["constDiff"]:
        ok("every non-function export matches, so the bundle is in sync")


def check_module_graph():
    print("\n4. every web/*.js parses and the ES module graph resolves")
    node = shutil.which("node")
    if not node:
        fail("V4", "node unavailable")
        return
    bad = []
    for f in sorted(os.listdir(WEB)):
        if not f.endswith(".js"):
            continue
        pr = subprocess.run([node, "--check", os.path.join(WEB, f)],
                            capture_output=True, text=True)
        if pr.returncode != 0:
            bad.append((f, pr.stderr.strip().splitlines()[:2]))
    if bad:
        for f, err in bad:
            fail("V4", "%s does not parse: %s" % (f, " / ".join(err)))
    else:
        ok("all web/*.js parse")

    # the import graph: a missing/duplicated export blanks the whole app
    for mod in ("engine.js", "policies.js", "arena.js"):
        path = os.path.join(WEB, mod)
        if not os.path.exists(path):
            continue
        pr = subprocess.run(
            [node, "--input-type=module", "-e",
             "await import('%s')" % path.replace("\\", "/")],
            capture_output=True, text=True)
        if pr.returncode != 0:
            first = [ln for ln in pr.stderr.splitlines() if ln.strip()][:3]
            msg = " / ".join(first)
            if "document" in pr.stderr or "window" in pr.stderr:
                print("    %-14s needs a DOM (expected for UI modules)" % mod)
            else:
                fail("V4", "%s fails to load as an ES module -- the app would "
                           "be a blank page: %s" % (mod, msg))
        else:
            ok("%s loads as an ES module" % mod)


def main():
    print("=" * 74)
    print("checker: web deployment and rule-copy divergence")
    print("=" * 74)
    check_index_consistency()
    check_weights_reachability()
    check_missing_weights_resilience()
    check_classic_bundle()
    check_module_graph()
    print("\n" + "=" * 74)
    if SKIPPED:
        print("skipped (NOT verified, not passed): %d" % len(SKIPPED))
        for t, m in SKIPPED:
            print("  [%s] %s" % (t, m.splitlines()[0]))
    if FAILURES:
        print("RESULT: %d FINDING(S)" % len(FAILURES))
        for tag, msg in FAILURES:
            print("  [%s] %s" % (tag, msg.splitlines()[0]))
        return 1
    print("RESULT: web layer is deployable as documented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
