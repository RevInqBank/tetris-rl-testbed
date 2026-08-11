"""Browser-driven checks of PROJECT.md verification criterion 4 (UI).

I previously reported these three as UNVERIFIABLE ("user's eyes are the only
instrument"). The lead installed playwright + chromium, so that is no longer
true. This file closes them:

  1. key input actually moves/rotates/drops the piece, and how fast
  2. AI infinite mode really restarts after a game over -- repeatedly
  3. 8 panels running at once do not stall the browser

Plus what a browser can check that a static read cannot:
  4. zero console errors / uncaught exceptions on load and during play
  5. the weights really load over HTTP from the documented serving root
  6. the app survives with the weights removed (no blank page)

Deliberately NOT claimed: whether the game FEELS good to play. Input latency
and frame pacing are measured; "손맛" stays a human judgement.

Run:  <ml python> tests/test_ui_playwright.py
      (needs playwright + chromium; run_all.sh picks the ml env)

Owner of the code under test: web.  This file is checker-owned.
"""

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


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    """Serves `directory` on loopback. Document root = project root, matching
    PROJECT.md (19:21 revision): URL is /web/index.html."""

    def __init__(self, directory):
        self.directory = directory
        self.port = free_port()
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(self.port),
             "--directory", self.directory],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/" % self.port,
                                       timeout=0.5)
                break
            except urllib.error.HTTPError:
                break
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

    @property
    def url(self):
        return "http://127.0.0.1:%d/web/index.html" % self.port


# ---------------------------------------------------------------------------

def attach_console(page, sink, http=None):
    page.on("console", lambda m: sink.append((m.type, m.text))
            if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: sink.append(("pageerror", str(e))))
    if http is not None:
        page.on("response", lambda r: http.append((r.status, r.url))
                if r.status >= 400 else None)
        page.on("requestfailed", lambda r: http.append(("FAILED", r.url)))


def boot(pw, url, sink, http=None):
    browser = pw.chromium.launch(args=["--no-sandbox",
                                       "--disable-dev-shm-usage"])
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    attach_console(page, sink, http)
    page.goto(url, wait_until="load", timeout=30000)
    page.wait_for_function("() => !!globalThis.TetrisUI", timeout=15000)
    # let loadWeights() settle
    page.wait_for_timeout(1500)
    return browser, page


# ---------------------------------------------------------------------------
# 1. load cleanliness + weights over HTTP
# ---------------------------------------------------------------------------

def check_load(page, sink, http):
    print("\n1. load: console clean, weights fetched over HTTP")
    # Report the missing RESOURCE, not a generic "console error" -- a 404 for a
    # data artefact and an uncaught exception are very different findings.
    if http:
        for status, url in http:
            name = url.rsplit("/", 1)[-1]
            degraded = page.evaluate("""() => {
              const t = document.body.innerText || '';
              return /eval_summary|데이터 누락|다시 만들면/.test(t);
            }""") if "eval_summary" in name else False
            if degraded:
                fail("U1", "%s %s -- the file is MISSING from weights/. The "
                           "app degrades honestly (the page says so in words, "
                           "not a blank table), but the cross-strategy "
                           "comparison the user actually wants is empty until "
                           "rl regenerates it." % (status, name))
            else:
                fail("U1", "%s for %s -- a resource the page requests is not "
                           "being served" % (status, url))
    pageerrs = [x for x in sink if x[0] == "pageerror"]
    if pageerrs:
        for t, m in pageerrs[:5]:
            fail("U1", "UNCAUGHT EXCEPTION on load: %s" % m[:300])
    conserr = [x for x in sink if x[0] == "error"
               and not any(u.rsplit("/", 1)[-1] in x[1] for _s, u in http)
               and "404" not in x[1]]
    if conserr:
        for t, m in conserr[:5]:
            fail("U1", "console error on load: %s" % m[:300])
    if not http and not pageerrs and not conserr:
        ok("no failed requests, no console errors, no uncaught exceptions "
           "during load")
    elif not pageerrs and not conserr:
        ok("no uncaught exceptions and no console errors beyond the missing "
           "resource above")

    info = page.evaluate("""() => {
      const P = globalThis.TetrisUI.P;
      const names = Object.keys(P.WEIGHTS || {});
      return {loaded: names, errors: (P.LOAD_ERRORS || []).map(String),
              status: (document.querySelector('#weights-status')||{}).textContent};
    }""")
    if not info["loaded"]:
        fail("U1", "no weights registered in the browser (LOAD_ERRORS=%r). "
                   "The AI panels would silently have nothing to run."
             % info["errors"][:3])
    else:
        ok("%d weight files loaded over HTTP in the real browser: %s"
           % (len(info["loaded"]), ", ".join(sorted(info["loaded"]))))
    if info["errors"]:
        fail("U1", "policies.js reported load errors: %r" % info["errors"][:3])

    # every strategy that claims to be ready must actually score a placement
    probe = page.evaluate("""() => {
      const {P, E} = globalThis.TetrisUI;
      const out = {};
      for (const st of P.STRATEGIES) {
        if (!P.isReady(st)) { out[st.id] = 'not-ready'; continue; }
        try {
          const s = E.newGame(12345);
          const ps = E.legalPlacements(s);
          const r = P.chooseAction(s, st.id, () => 0.5, 'normal');
          out[st.id] = (r && r.placement) ? 'ok' : 'no-placement';
        } catch (e) { out[st.id] = 'threw: ' + (e && e.message); }
      }
      return out;
    }""")
    bad = {k: v for k, v in probe.items()
           if v not in ("ok", "not-ready")}
    if bad:
        for k, v in bad.items():
            fail("U1", "strategy %s is registered but cannot choose an "
                       "action: %s" % (k, v))
    else:
        n = sum(1 for v in probe.values() if v == "ok")
        nr = [k for k, v in probe.items() if v == "not-ready"]
        ok("%d strategies choose a placement in-browser%s"
           % (n, ("; not ready: " + ", ".join(nr)) if nr else ""))


# ---------------------------------------------------------------------------
# 2. key input responsiveness
# ---------------------------------------------------------------------------

KEYS = [("ArrowLeft", "move left"), ("ArrowRight", "move right"),
        ("ArrowUp", "rotate CW"), ("ArrowDown", "soft drop"),
        ("KeyZ", "rotate CCW"), ("KeyC", "hold")]


def check_keys(page):
    print("\n2. key input: does a key press change the game state?")
    # autoplay must be OFF or ui.js ignores keys by design
    page.evaluate("""() => {
      const cb = document.querySelector('#opt-autoplay');
      if (cb.checked) { cb.checked = false; cb.dispatchEvent(new Event('change')); }
      globalThis.TetrisUI.play.reset();
    }""")
    page.wait_for_timeout(200)
    page.focus("body")

    def snap():
        return page.evaluate("""() => {
          const g = globalThis.TetrisUI.play.game;
          const a = g.active;
          return {c: a ? a.c : null, r: a ? a.r : null,
                  rot: a ? a.rot : null, score: g.score,
                  pieces: g.pieces, held: g.heldType};
        }""")

    moved = {}
    latencies = []
    for key, label in KEYS:
        before = snap()
        t0 = time.time()
        page.keyboard.press(key)
        # poll until something changes (bounded)
        changed = False
        for _ in range(60):
            after = snap()
            if after != before:
                changed = True
                break
            page.wait_for_timeout(5)
        dt = (time.time() - t0) * 1000
        moved[label] = changed
        if changed:
            latencies.append(dt)
        # reset position so the next key has room
        page.evaluate("() => globalThis.TetrisUI.play.reset()")
        page.wait_for_timeout(120)

    dead = [k for k, v in moved.items() if not v]
    if dead:
        fail("U2", "these keys produced NO state change: %s -- the user cannot "
                   "play" % ", ".join(dead))
    else:
        ok("all %d control keys change the game state (%s)"
           % (len(KEYS), ", ".join(k for k, _ in
                                   [(l, v) for l, v in moved.items()])))
    if latencies:
        worst = max(latencies)
        avg = sum(latencies) / len(latencies)
        if worst > 120:
            fail("U2", "slowest key took %.0f ms to show an effect (avg %.0f "
                       "ms). Above ~100 ms the input feels laggy."
                 % (worst, avg))
        else:
            ok("key -> visible state change: avg %.0f ms, worst %.0f ms "
               "(measured by polling, so this is an upper bound)"
               % (avg, worst))

    # hard drop must lock a piece and advance the counter
    before = snap()
    page.keyboard.press("Space")
    page.wait_for_timeout(200)
    after = snap()
    if after["pieces"] <= before["pieces"]:
        fail("U2", "Space (hard drop) did not lock a piece: pieces %d -> %d"
             % (before["pieces"], after["pieces"]))
    else:
        ok("Space hard-drops and locks: pieces %d -> %d, score %d -> %d"
           % (before["pieces"], after["pieces"], before["score"],
              after["score"]))


# ---------------------------------------------------------------------------
# 3. AI infinite mode: does it really restart, repeatedly?
# ---------------------------------------------------------------------------

def check_autoplay_restart(page):
    print("\n3. AI infinite mode: restart after game over (repeatedly)")
    # A strong policy will not die inside a test budget, and injecting a dead
    # board behind the engine's back tests nothing real (the engine's own
    # game-over bookkeeping is exactly what must fire). So drive the REAL path:
    # pick the weakest agent, which tops out in ~20-30 pieces on its own.
    agents = page.evaluate("""() => {
      const sel = document.querySelector('#opt-agent');
      return Array.from(sel.options).map(o => o.value);
    }""")
    weak = next((a for a in agents if "random" in a.lower()), None)
    if weak is None:
        skip("U3", "no 'random' agent in #opt-agent (%r) -- cannot make the AI "
                   "die on its own, and injecting a dead board would not "
                   "exercise the real game-over path" % agents)
        return
    print("      using the weakest agent (%r) so death is reached naturally"
          % weak)
    page.evaluate("""(weak) => {
      const sel = document.querySelector('#opt-agent');
      sel.value = weak; sel.dispatchEvent(new Event('change'));
      const cb = document.querySelector('#opt-autoplay');
      if (!cb.checked) { cb.checked = true; cb.dispatchEvent(new Event('change')); }
      const sp = document.querySelector('#ai-speed');
      if (sp) { sp.value = sp.max; sp.dispatchEvent(new Event('input')); }
      globalThis.TetrisUI.play.reset();
    }""", weak)
    page.wait_for_timeout(400)

    start_games = page.evaluate(
        "() => globalThis.TetrisUI.play.aiStats.games")
    want = 4
    try:
        page.wait_for_function(
            "(n) => globalThis.TetrisUI.play.aiStats.games >= n",
            arg=start_games + want, timeout=40000)
    except Exception:
        # Diagnose rather than just report a timeout: what is it stuck on?
        diag = page.evaluate("""() => {
          const p = globalThis.TetrisUI.play, g = p.game;
          const a0 = g.active;
          const before = {rot: a0 && a0.rot, c: a0 && a0.c, r: a0 && a0.r};
          const seq = [];
          for (let i = 0; i < 6; i++) seq.push(g.stepToward(p.aiTarget));
          const a1 = g.active;
          return {games: p.aiStats.games, over: g.gameOver,
                  pieces: g.pieces, lines: g.lines,
                  target: p.aiTarget, before,
                  after: {rot: a1 && a1.rot, c: a1 && a1.c, r: a1 && a1.r},
                  seq, aiStep: [p.aiStep(), p.aiStep(), p.aiStep()]};
        }""")
        stuck_on = None
        if diag["seq"] and len(set(diag["seq"])) == 1 and \
                diag["before"] == diag["after"]:
            stuck_on = diag["seq"][0]
        msg = ("PLAY-screen AI infinite mode FREEZES mid-game and never "
               "declares game over. After %d game(s) it stopped at %d pieces "
               "with game_over=false, and stayed there for 40 s."
               % (diag["games"], diag["pieces"]))
        if stuck_on:
            msg += (" Mechanism: the target placement is %r (rot=%d, x=%d) but "
                    "the piece sits at rot=%s x=%s, and Game.stepToward() "
                    "returns '%s' six times in a row WITHOUT the piece moving "
                    "-- move()/rotate() are blocked by the stack at the spawn "
                    "row, and stepToward does not check whether they "
                    "succeeded. play.aiStep() therefore returns %r forever, so "
                    "the frame loop spins on a no-op, gameOver() never fires, "
                    "and infinite mode never restarts."
                    % (diag["target"], diag["target"][0], diag["target"][1],
                       diag["before"]["rot"], diag["before"]["c"], stuck_on,
                       diag["aiStep"]))
        else:
            msg += (" stepToward returned %r with state %r -> %r; aiStep %r."
                    % (diag["seq"], diag["before"], diag["after"],
                       diag["aiStep"]))
        fail("U3", msg)
        print("      NOTE: the ARENA screen does NOT have this bug -- it calls "
              "applyPlacement directly instead of animating, which is why its "
              "random panel restarts fine while PLAY freezes.")
        return
    end_games = page.evaluate(
        "() => globalThis.TetrisUI.play.aiStats.games")
    live = page.evaluate("""() => {
      const g = globalThis.TetrisUI.play.game;
      return {over: g.gameOver, pieces: g.pieces};
    }""")
    if live["over"]:
        fail("U3", "after %d restarts the current game is left in game_over -- "
                   "the loop stopped instead of restarting"
             % (end_games - start_games))
        return
    ok("the AI died and restarted on its own %d times in under 40 s (games "
       "%d -> %d) and is mid-game again (%d pieces), so infinite mode really "
       "loops through the engine's own game-over path"
       % (end_games - start_games, start_games, end_games, live["pieces"]))

    # It keeps playing on its own afterwards. Measure CUMULATIVE progress:
    # game.pieces resets on every restart, and at max speed the weak agent
    # churns ~20 games/second, so sampling the per-game counter twice lands on
    # a fresh game and reads 0 -> 0 for a perfectly healthy loop.
    def cum():
        return page.evaluate("""() => {
          const p = globalThis.TetrisUI.play;
          return {games: p.aiStats.games, pieces: p.aiStats.pieces};
        }""")
    c0 = cum()
    page.wait_for_timeout(1500)
    c1 = cum()
    adv_p = c1["pieces"] - c0["pieces"]
    adv_g = c1["games"] - c0["games"]
    if adv_p <= 0 and adv_g <= 0:
        fail("U3", "after the restarts the AI stopped entirely: cumulative "
                   "pieces %d -> %d and games %d -> %d in 1.5 s"
             % (c0["pieces"], c1["pieces"], c0["games"], c1["games"]))
    else:
        ok("AI keeps playing unattended after the restarts: +%d pieces and "
           "+%d games in 1.5 s (cumulative counters, since the per-game one "
           "resets on every restart)" % (adv_p, adv_g))

    stats = page.evaluate("""() => ({
      games: document.querySelector('#ai-games').textContent,
      best: document.querySelector('#ai-best').textContent,
      mean: document.querySelector('#ai-mean').textContent,
    })""")
    print("      HUD after the loop: games=%s best=%s mean=%s"
          % (stats["games"], stats["best"], stats["mean"]))


# ---------------------------------------------------------------------------
# 4. eight panels at once
# ---------------------------------------------------------------------------

def check_arena(page, sink):
    print("\n4. ARENA: 8 panels running simultaneously")
    before_errs = len(sink)
    page.evaluate("() => globalThis.TetrisUI.showScreen('arena')")
    page.wait_for_timeout(400)
    n_panels = page.evaluate(
        "() => globalThis.TetrisUI.arena.panels.length")
    # #ar-toggle TOGGLES. Clicking it blindly stops an already-running arena
    # (which is what my first pass did, producing a bogus fps=0 "failure").
    # Drive the state, not the button.
    was = page.evaluate("() => globalThis.TetrisUI.arena.running")
    print("      arena.running on entering the screen: %s" % was)
    if not was:
        page.evaluate("() => globalThis.TetrisUI.arena.start()")
    if not page.evaluate("() => globalThis.TetrisUI.arena.running"):
        fail("U4", "arena.start() did not set running=true -- the 8-panel view "
                   "cannot run at all")
        return
    RUN_MS = 12000
    t0 = time.time()
    samples = []
    while (time.time() - t0) * 1000 < RUN_MS:
        page.wait_for_timeout(1500)
        samples.append(page.evaluate("""() => {
          const a = globalThis.TetrisUI.arena;
          return {fps: a.fps, running: a.running,
                  pieces: a.panels.map(p => p.pieces),
                  games: a.panels.map(p => p.games),
                  dead: a.panels.filter(p => p.dead).length,
                  ready: a.panels.filter(p => p.ready).length,
                  total: (document.querySelector('#ar-total-pieces')||{}).textContent};
        }"""))
    page.evaluate("() => globalThis.TetrisUI.arena.stop()")

    print("      panels in the grid: %d" % n_panels)
    for i, s in enumerate(samples):
        print("      t+%4.1fs  fps=%-4s ready=%s dead=%s total_pieces=%-7s"
              % (1.5 * (i + 1), s["fps"], s["ready"], s["dead"], s["total"]))

    if n_panels < 8:
        fail("U4", "only %d panels in arena.panels, PROJECT.md specifies 8"
             % n_panels)
    else:
        ok("%d panels in the arena grid (PROJECT.md asks for 8)" % n_panels)

    fpss = [s["fps"] for s in samples if isinstance(s["fps"], (int, float))
            and s["fps"] > 0]
    if not fpss:
        fail("U4", "the arena reported no fps at all -- it never ran")
    else:
        worst = min(fpss)
        if worst < 20:
            fail("U4", "fps dropped to %d with 8 panels running (samples %r). "
                       "PROJECT.md criterion 4 requires the browser not to "
                       "stall." % (worst, fpss))
        else:
            ok("8 panels ran for %.0f s at fps %r -- worst sample %d, never "
               "stalled" % (RUN_MS / 1000.0, fpss, worst))

    # every READY panel must actually advance; a panel that restarts resets
    # its piece counter, so count total progress as pieces + games instead
    first, last = samples[0], samples[-1]
    ready_n = last["ready"]
    prog0 = [p + 1000 * g for p, g in zip(first["pieces"], first["games"])]
    prog1 = [p + 1000 * g for p, g in zip(last["pieces"], last["games"])]
    stalled = [i for i in range(len(prog1)) if prog1[i] <= prog0[i]]
    if len(prog1) != n_panels:
        skip("U4", "panel count changed mid-run; per-panel progress not judged")
    elif stalled and len(stalled) == n_panels:
        fail("U4", "NO panel advanced in %.0f s -- the arena is frozen"
             % (RUN_MS / 1000.0))
    elif stalled:
        # a not-ready panel (missing weights) legitimately cannot advance
        names = page.evaluate(
            "(ix) => ix.map(i => globalThis.TetrisUI.arena.panels[i].st.id"
            " + (globalThis.TetrisUI.arena.panels[i].ready ? '' : '(not-ready)'))",
            stalled)
        unready = [n for n in names if "(not-ready)" in n]
        if len(unready) == len(names):
            ok("all %d ready panels advanced; the %d that did not are "
               "not-ready by design (no weights): %s"
               % (ready_n, len(names), ", ".join(names)))
        else:
            fail("U4", "these READY panels did not advance a single piece in "
                       "%.0f s while the others ran: %s"
                 % (RUN_MS / 1000.0,
                    ", ".join(n for n in names if "(not-ready)" not in n)))
    else:
        ok("all %d panels advanced over %.0f s (pieces %r -> %r, games %r)"
           % (n_panels, RUN_MS / 1000.0, first["pieces"], last["pieces"],
              last["games"]))

    new_errs = sink[before_errs:]
    hard = [x for x in new_errs if x[0] in ("error", "pageerror")]
    if hard:
        for t, m in hard[:5]:
            fail("U4", "console %s while 8 panels ran: %s" % (t, m[:300]))
    else:
        ok("no console errors or uncaught exceptions during the 8-panel run")


# ---------------------------------------------------------------------------
# 5. no weights at all -> still usable
# ---------------------------------------------------------------------------

def check_no_weights(pw):
    print("\n5. with the weights removed, does the app still come up?")
    tmp = tempfile.mkdtemp()
    try:
        web_dst = os.path.join(tmp, "web")
        os.makedirs(web_dst)
        src = os.path.join(_ROOT, "web")
        for f in os.listdir(src):
            p = os.path.join(src, f)
            if os.path.isfile(p) and not os.path.islink(p):
                shutil.copy(p, os.path.join(web_dst, f))
        with Server(tmp) as srv:
            sink = []
            browser, page = boot(pw, srv.url, sink)
            try:
                state = page.evaluate("""() => {
                  const P = globalThis.TetrisUI.P;
                  return {ui: !!globalThis.TetrisUI,
                          weights: Object.keys(P.WEIGHTS || {}).length,
                          board: !!document.querySelector('#play-board')};
                }""")
                # a human must still be able to play
                page.evaluate("() => globalThis.TetrisUI.play.reset()")
                page.focus("body")
                p0 = page.evaluate(
                    "() => globalThis.TetrisUI.play.game.pieces")
                page.keyboard.press("Space")
                page.wait_for_timeout(250)
                p1 = page.evaluate(
                    "() => globalThis.TetrisUI.play.game.pieces")
                pageerrs = [x for x in sink if x[0] == "pageerror"]
                if pageerrs:
                    fail("U5", "uncaught exception with no weights: %s"
                         % pageerrs[0][1][:250])
                elif not state["ui"] or not state["board"]:
                    fail("U5", "app did not initialise without weights")
                elif p1 <= p0:
                    fail("U5", "human play is broken without weights "
                               "(hard drop did nothing)")
                else:
                    ok("no weights (%d registered): app initialises, no "
                       "uncaught exception, and a human can still hard-drop "
                       "(pieces %d -> %d)" % (state["weights"], p0, p1))
            finally:
                browser.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------

def main():
    print("=" * 74)
    print("checker: browser-driven UI checks (PROJECT.md criterion 4)")
    print("  read at %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 74)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        skip("U0", "playwright not importable in this interpreter (%s) -- ALL "
                   "browser checks were NOT run. Use the ml env python." % e)
        report()
        return 1 if FAILED else 0

    with sync_playwright() as pw:
        with Server(_ROOT) as srv:
            print("  serving project root at %s" % srv.url)
            sink, http = [], []
            browser, page = boot(pw, srv.url, sink, http)
            try:
                check_load(page, sink, http)
                check_keys(page)
                check_autoplay_restart(page)
                check_arena(page, sink)
            finally:
                browser.close()
        check_no_weights(pw)
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
        print("FAILURES:")
        for t, m in FAILED:
            print("  [%s] %s" % (t, m.splitlines()[0]))
        return 1
    print("RESULT: the UI criteria hold in a real browser.")
    print("NOT claimed: whether it FEELS good. Latency and frame pacing are")
    print("measured above; 손맛 is still the user's call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
