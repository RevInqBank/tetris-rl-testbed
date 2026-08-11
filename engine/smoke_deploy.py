"""Deploy smoke test: does the SERVED app actually come up?

The gap this closes: every other check in this repo tests code in isolation.
Nothing tested the thing the user actually touches -- an HTTP server handing
out index.html and everything it references. Two bugs already escaped through
that gap:

  * index.html referenced ../weights/, which 404s depending on document root
  * policies.js read info.landing_height, which silently became a constant 0

Neither produced an exception. A 404 asset and a silently-absent field look
identical from inside the code: fine.

What this does
--------------
1. Serves the project root on 127.0.0.1 at an unused high port (never 8080 --
   that one belongs to the user) in a background thread.
2. Fetches index.html, parses out every referenced asset -- <script src>,
   <link href>, <img src>, ES import specifiers, and fetch('...') paths found
   inside the served JS -- and GETs each one. Anything that is not 200 fails.
3. Fetches every weights/*.json, parses it, and checks for meta.parity_verified.
4. Re-runs the generated-artifact staleness checks.
5. Shuts the server down in a finally block, always.

Output rules (team rule, from the parity.py "node failed" incident):
  * PASS / FAIL / SKIP are three different things and are counted separately.
  * "could not run because of the environment" is never reported as success,
    and never as failure either -- it is a SKIP, and the skip count is always
    printed in the summary.

Usage:
    python3 engine/smoke_deploy.py
    python3 engine/smoke_deploy.py --port 8099 --verbose
"""

import json
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
WEB_DIR = os.path.join(ROOT, "web")
WEIGHTS_DIR = os.path.join(ROOT, "weights")

#: Bind loopback only. The single externally reachable port is the user's 8080,
#: which this script must never touch.
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8099
FORBIDDEN_PORTS = {8080}

#: Entry documents to crawl, relative to the served root.
ENTRY_PAGES = ["web/index.html"]


class Result:
    """PASS / FAIL / SKIP tally. Skips are first-class, never swallowed."""

    def __init__(self, verbose=False):
        self.passed = []
        self.failed = []
        self.skipped = []
        self.verbose = verbose

    def ok(self, name, detail=""):
        self.passed.append(name)
        print("PASS %s%s" % (name, ("  " + detail) if detail else ""))

    def fail(self, name, detail=""):
        self.failed.append(name)
        print("FAIL %s%s" % (name, ("  " + detail) if detail else ""))

    def skip(self, name, why):
        self.skipped.append((name, why))
        print("SKIP %s  (%s)" % (name, why))

    def note(self, msg):
        if self.verbose:
            print("     %s" % msg)

    def summary(self) -> int:
        print("")
        print("-" * 68)
        if self.skipped:
            print("%d check(s) SKIPPED -- these verified NOTHING:"
                  % len(self.skipped))
            for name, why in self.skipped:
                print("     %s  (%s)" % (name, why))
        print("deploy smoke: %d passed, %d failed, %d skipped"
              % (len(self.passed), len(self.failed), len(self.skipped)))
        if self.failed:
            print("FAILED:")
            for name in self.failed:
                print("     %s" % name)
        return 1 if self.failed else 0


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without the per-request logging noise."""

    def log_message(self, fmt, *args):
        pass


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((BIND_HOST, port))
            return True
        except OSError:
            return False


def pick_port(preferred: int) -> int:
    """First free port at or above `preferred`, skipping the user's port."""
    port = preferred
    for _ in range(50):
        if port not in FORBIDDEN_PORTS and _port_free(port):
            return port
        port += 1
    raise RuntimeError("no free port near %d" % preferred)


def start_server(port: int):
    handler = partial(_QuietHandler, directory=ROOT)
    httpd = ThreadingHTTPServer((BIND_HOST, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


# ---------------------------------------------------------------------------
# fetching and parsing
# ---------------------------------------------------------------------------

def fetch(base: str, path: str, timeout: float = 10.0) -> tuple:
    """GET one path. Returns ``(status, body_bytes_or_None, error_or_None)``."""
    url = base + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read(), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, None
    except Exception as exc:                       # connection level failure
        return None, None, exc


#: HTML asset references.
_HTML_REFS = [
    re.compile(r"""<script[^>]+src\s*=\s*["']([^"']+)["']""", re.I),
    re.compile(r"""<link[^>]+href\s*=\s*["']([^"']+)["']""", re.I),
    re.compile(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", re.I),
]

#: JS references: static imports, dynamic imports, and fetch() of a literal.
_JS_REFS = [
    re.compile(r"""(?:^|\s)import\s[^'\"]*from\s*['\"]([^'\"]+)['\"]""", re.M),
    re.compile(r"""(?:^|\s)import\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"""),
    re.compile(r"""fetch\s*\(\s*['\"]([^'\"]+)['\"]"""),
    re.compile(r"""(?:^|\s)export\s[^'\"]*from\s*['\"]([^'\"]+)['\"]""", re.M),
    # new URL('./x/', import.meta.url) -- how policies.js builds its weight
    # base URLs. Without this the crawler silently skips the whole weights
    # directory and still reports "all assets 200", which is the exact kind of
    # false reassurance this script exists to prevent.
    re.compile(r"""new\s+URL\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*import\.meta\.url"""),
]

#: References the crawler cannot resolve statically, so it must say so rather
#: than let them pass unmentioned.
_DYNAMIC_HINTS = [
    re.compile(r"""fetch\s*\(\s*[`]"""),            # template-literal URL
    re.compile(r"""fetch\s*\(\s*[A-Za-z_$]"""),     # variable URL
    re.compile(r"""import\s*\(\s*[`A-Za-z_$]"""),   # computed import
]


def extract_refs(text: str, is_html: bool) -> list:
    """Referenced paths found in one document, in first-seen order."""
    out = []
    for pattern in (_HTML_REFS if is_html else []) + _JS_REFS:
        for match in pattern.finditer(text):
            ref = match.group(1).strip()
            if ref and ref not in out:
                out.append(ref)
    return out


def count_dynamic_refs(text: str) -> int:
    """Number of references built at runtime, which the crawler cannot follow."""
    return sum(len(p.findall(text)) for p in _DYNAMIC_HINTS)


def resolve(base_path: str, ref: str):
    """Resolve a reference against the document that contains it.

    Returns a served path starting with '/', or None if the reference points
    outside the served tree or is not a local file at all.
    """
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref) or ref.startswith("//"):
        return None                            # http:, data:, blob:, mailto:
    if ref.startswith("#") or ref.startswith("?"):
        return None
    ref = ref.split("#")[0].split("?")[0]
    if not ref:
        return None
    # Preserve a trailing slash: it marks a directory BASE (one of several
    # candidates the app may try) rather than a required asset. normpath drops
    # it, which made a legitimate fallback base look like a broken asset.
    is_dir = ref.endswith("/")
    if ref.startswith("/"):
        joined = os.path.normpath(ref)
    else:
        joined = os.path.normpath(os.path.join(os.path.dirname(base_path), ref))
    joined = joined.replace(os.sep, "/")
    if is_dir and not joined.endswith("/"):
        joined += "/"
    if joined.startswith(".."):
        # Escapes the served root -- this is exactly the ../weights/ class of
        # bug, so surface it as a reference we tried and could not serve.
        return "/" + joined.lstrip("/")
    return "/" + joined.lstrip("/")


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def check_pages(base: str, res: Result) -> None:
    """Crawl each entry page and GET every asset it transitively references."""
    for page in ENTRY_PAGES:
        page_path = "/" + page
        status, body, err = fetch(base, page_path)
        if err is not None:
            res.fail("serve %s" % page, "connection error: %r" % (err,))
            continue
        if status != 200:
            res.fail("serve %s" % page, "HTTP %s" % status)
            continue
        res.ok("serve %s" % page, "HTTP 200, %d bytes" % len(body))

        # Breadth-first over local references, following JS into JS.
        seen = {page_path}
        queue = [(page_path, body.decode("utf-8", "replace"), True)]
        checked = 0
        dynamic = 0
        dirs_seen = []          # discovery order matters -- see check_weight_bases
        while queue:
            doc_path, text, is_html = queue.pop(0)
            dynamic += count_dynamic_refs(text)
            for ref in extract_refs(text, is_html):
                target = resolve(doc_path, ref)
                if target is None or target in seen:
                    continue
                seen.add(target)
                if target.endswith("/"):
                    # A directory base (e.g. the weights/ base policies.js
                    # builds). Record it; the weights check probes it properly.
                    if target not in dirs_seen:
                        dirs_seen.append(target)
                    continue
                st, bd, er = fetch(base, target)
                checked += 1
                if er is not None:
                    res.fail("asset %s" % target,
                             "referenced by %s -- connection error %r"
                             % (doc_path, er))
                    continue
                if st != 200:
                    res.fail("asset %s" % target,
                             "referenced by %s -- HTTP %s" % (doc_path, st))
                    continue
                res.note("asset %s -> 200 (%d bytes)" % (target, len(bd)))
                if target.endswith(".js"):
                    queue.append((target, bd.decode("utf-8", "replace"), False))
        if checked:
            res.ok("all %d asset(s) referenced from %s return 200"
                   % (checked, page))
        else:
            res.skip("asset crawl for %s" % page,
                     "no local references were found to check")

        if dirs_seen:
            res.note("directory bases referenced, in source order: %s"
                     % dirs_seen)
        if dynamic:
            res.note("%d runtime-built URL(s) found in %s; the weights ones are "
                     "covered explicitly below" % (dynamic, page))
        check_weight_bases(base, dirs_seen, res)


#: The files the app actually GETs after it resolves a weights base, per web.
#: Split by severity: without a CRITICAL file the matching ARENA panel shows
#: "untrained" and never runs, so it is a deploy failure. The OPTIONAL two only
#: cost the comparison table if missing, and the app falls back to a known stem
#: list -- reporting them as failures would make this script cry wolf.
WEIGHT_FILES_CRITICAL = [
    "cem_linear.json",
    "search_1ply.json",
    "reinforce.json",
    "reinforce_baseline.json",
    "a2c.json",
    "dqn.json",
]
WEIGHT_FILES_OPTIONAL = [
    "index.json",
    "eval_summary.json",
]


def check_runtime_weight_fetches(base: str, weights_base: str,
                                 res: Result) -> None:
    """GET the files the app fetches at runtime from its resolved base.

    These URLs are built with `new URL(..., import.meta.url)` and probed in
    order, so a static crawl cannot enumerate them. The list comes from web and
    is checked explicitly -- otherwise the whole weights directory sits behind a
    SKIP and "all assets 200" overstates what was verified.
    """
    missing_critical = []
    missing_optional = []
    for name in WEIGHT_FILES_CRITICAL + WEIGHT_FILES_OPTIONAL:
        st, _bd, er = fetch(base, weights_base + name)
        if er is None and st == 200:
            continue
        if name in WEIGHT_FILES_CRITICAL:
            missing_critical.append((name, st))
        else:
            missing_optional.append((name, st))

    if missing_critical:
        res.fail("runtime weight fetches from %s" % weights_base,
                 "MISSING %s -- the matching ARENA panel would show "
                 "'untrained' and never run"
                 % ", ".join("%s(HTTP %s)" % (n, s) for n, s in missing_critical))
    else:
        res.ok("runtime weight fetches from %s" % weights_base,
               "all %d policy file(s) served" % len(WEIGHT_FILES_CRITICAL))

    if missing_optional:
        res.ok("optional weights/ extras absent (app degrades cleanly)",
               ", ".join("%s(HTTP %s)" % (n, s) for n, s in missing_optional))
    else:
        res.ok("optional weights/ extras served",
               ", ".join(WEIGHT_FILES_OPTIONAL))


def check_weight_bases(base: str, dirs: list, res: Result) -> None:
    """Probe each weights base URL the app is willing to try.

    policies.js tries candidates IN SOURCE ORDER and takes the first that
    answers, so `dirs` must arrive in that order: probing a later fallback
    instead would verify a directory the app never reads. At least one must
    work, or every policy silently falls back to no weights -- which looks like
    "the training is weak", not like a bug.
    """
    candidates = [d for d in dirs if "weight" in d.lower()]
    if not candidates:
        res.skip("weights base URL reachable",
                 "no weights directory reference found in the served JS")
        return
    working = []
    for d in candidates:
        st, _bd, er = fetch(base, d + "index.json")
        if er is None and st == 200:
            working.append(d)
    if working:
        res.ok("weights base URL reachable",
               "%s (of %d candidate base(s); the others are fallbacks and may "
               "404 by design)" % (", ".join(working), len(candidates)))
        # working[0] is the first candidate IN SOURCE ORDER, which is the one
        # the app will actually resolve to.
        check_runtime_weight_fetches(base, working[0], res)
    else:
        res.fail("weights base URL reachable",
                 "none of %r served index.json -- policies would load no "
                 "weights and merely look undertrained" % candidates)


def deployed_policy_names(base: str) -> tuple:
    """Policy files the app will actually load, per its own manifest.

    weights/ also accumulates files the app never reads: training checkpoints,
    eval reports, and in-progress artifacts from a run that is still going. The
    manifest (weights/index.json, the same file the app reads) is the authority
    on what is deployed.

    Returns ``(deployed_set, manifest_available)``. If the manifest cannot be
    read, every policy file is treated as deployed -- failing closed, because
    guessing a smaller surface would let an unverified file through.
    """
    st, body, err = fetch(base, "/weights/index.json")
    if err is not None or st != 200 or body is None:
        return set(), False
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        return set(), False
    names = set()
    for entry in data.get("strategies") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("weights", "file", "path"):
            val = entry.get(key)
            if isinstance(val, str) and val.endswith(".json"):
                names.add(os.path.basename(val))
    if not names:
        # Parsed but yielded nothing. Indistinguishable from a schema change,
        # and it must NOT be read as "nothing is deployed" -- that would exempt
        # every policy from the provenance gate. Fail closed.
        #
        # This is not hypothetical: the manifest's key changed from "weights" to
        # "file" mid-session. A parser watching only the old key would have
        # silently switched the gate off while still reporting all-green.
        return set(), False
    return names, True


def manifest_listed_files_served(base: str, res: Result) -> None:
    """Every policy file the manifest lists must actually be served.

    The manifest is the app's shopping list: it enumerates strategies and then
    fetches each one. A listed-but-absent file is a 404 at runtime and that
    panel never runs.

    check_weights cannot catch this -- it iterates files that EXIST on disk, so
    a file that is listed and missing is invisible to it. That is how
    cem_score_wells.json sat in the manifest with no file behind it while the
    gate reported all-green.
    """
    listed, ok = deployed_policy_names(base)
    if not ok:
        res.skip("manifest-listed files are served",
                 "manifest unreadable or yielded no entries")
        return
    missing = []
    for name in sorted(listed):
        st, _bd, er = fetch(base, "/weights/" + name)
        if er is not None or st != 200:
            missing.append((name, st))
    if missing:
        res.fail("manifest-listed files are served",
                 "weights/index.json lists %s but they are NOT served -- the "
                 "app will 404 and those panels will not run"
                 % ", ".join("%s(HTTP %s)" % (n, s) for n, s in missing))
    else:
        res.ok("manifest-listed files are served",
               "all %d file(s) named by weights/index.json return 200"
               % len(listed))


def check_weights(base: str, res: Result) -> None:
    """Served, parseable, and -- if deployed -- carrying parity provenance.

    Provenance is GATING only for files the app actually loads. Requiring it of
    every file in weights/ produced a false failure on an artifact from a
    training run still in progress, which the app never reads. A deploy gate
    that fails for non-deploy reasons is a gate people learn to ignore, and
    that is worse than not having one.
    """
    if not os.path.isdir(WEIGHTS_DIR):
        res.skip("weights/*.json", "weights/ directory does not exist")
        return
    names = sorted(n for n in os.listdir(WEIGHTS_DIR) if n.endswith(".json"))
    if not names:
        res.skip("weights/*.json",
                 "no .json files present yet (rl has not published any)")
        return

    deployed, manifest_ok = deployed_policy_names(base)
    if not manifest_ok:
        res.skip("scope policy provenance to the deployed set",
                 "weights/index.json unreadable -- treating EVERY policy file "
                 "as deployed (failing closed)")

    policies = 0
    auxiliary = []
    undeployed = []
    for name in names:
        path = "/weights/" + name
        status, body, err = fetch(base, path)
        if err is not None:
            res.fail("weights %s" % name, "connection error: %r" % (err,))
            continue
        if status != 200:
            res.fail("weights %s" % name, "HTTP %s" % status)
            continue
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            res.fail("weights %s" % name, "served but not valid JSON: %s" % exc)
            continue

        # Only policy weight files carry the rl->web exchange format (PROJECT.md
        # "가중치 교환 포맷": name/kind/features/weights|layers/meta). Manifests,
        # eval reports and training checkpoints also live in weights/ and must
        # NOT be held to the parity_verified contract -- demanding it of them
        # produced four false failures, and a smoke test that cries wolf is one
        # nobody reads.
        if not (isinstance(data, dict) and "kind" in data):
            auxiliary.append(name)
            continue

        policies += 1
        is_deployed = (not manifest_ok) or (name in deployed)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else None
        verified = meta.get("parity_verified") if meta else None

        if verified is True:
            res.ok("policy %s" % name,
                   "HTTP 200, kind=%s, parity_verified=True%s"
                   % (data.get("kind"), "" if is_deployed else " (not deployed)"))
            continue

        # Present-but-null/false is NOT provenance. Accepting `None` would
        # repeat the `|| 0` mistake: a missing value dressed up as an answer.
        why = ("no meta object" if meta is None
               else "meta.parity_verified is %r, not True" % (verified,))
        if is_deployed:
            res.fail("policy %s" % name,
                     "%s -- the app LOADS this file (weights/index.json lists "
                     "it) and it carries no proof it was trained against the "
                     "verified engine" % why)
        else:
            undeployed.append((name, why))

    if auxiliary:
        res.ok("%d auxiliary weights/ file(s) served and parse"
               % len(auxiliary), ", ".join(auxiliary))
        res.note("auxiliary files are exempt from meta.parity_verified "
                 "(no `kind` field, so not policy weights)")
    if undeployed:
        # Not a gate failure: the manifest does not list these, so no user ever
        # loads them. Still reported by name -- silence here is how an
        # unverified file later gets added to the manifest unnoticed.
        res.ok("%d policy file(s) present but NOT deployed, provenance not "
               "gated" % len(undeployed),
               "; ".join("%s (%s)" % (n, w) for n, w in undeployed))

    if policies == 0:
        res.skip("policy weight contract",
                 "no file in weights/ has a `kind` field yet")


def check_generated_artifacts(res: Result) -> None:
    """The served JS must not be a stale build of the Python tables."""
    sys.path.insert(0, HERE)
    import contextlib
    import io as _io

    for module_name, label, source in (
            ("gen_tables_js", "web/tables.js", "engine/tables.py"),
            ("gen_classic_bundle", "web/engine.classic.js", "the ES modules")):
        try:
            mod = __import__(module_name)
        except Exception as exc:
            res.skip("%s freshness" % label, "generator not importable: %r" % exc)
            continue
        buf = _io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = mod.main(["--check"])
        except Exception as exc:
            res.fail("%s freshness" % label, "checker raised: %r" % exc)
            continue
        if rc == 0:
            res.ok("%s is in sync with %s" % (label, source))
        else:
            res.fail("%s is STALE" % label, buf.getvalue().strip())


# ---------------------------------------------------------------------------

def main(argv) -> int:
    verbose = "--verbose" in argv or "-v" in argv
    port = DEFAULT_PORT
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    if port in FORBIDDEN_PORTS:
        print("refusing to use port %d -- that is the user's server" % port)
        return 2

    res = Result(verbose=verbose)

    print("=== generated artifacts ===")
    check_generated_artifacts(res)

    print("\n=== served app (%s, loopback only) ===" % ROOT)
    try:
        port = pick_port(port)
    except RuntimeError as exc:
        res.skip("HTTP smoke test", "could not bind a port: %s" % exc)
        return res.summary()

    httpd = None
    try:
        httpd, _thread = start_server(port)
        base = "http://%s:%d" % (BIND_HOST, port)
        print("serving %s on %s" % (ROOT, base))

        status, _body, err = fetch(base, "/", timeout=5.0)
        if err is not None:
            res.skip("HTTP smoke test",
                     "server did not answer on %s: %r" % (base, err))
            return res.summary()
        res.ok("server responds", "GET / -> HTTP %s" % status)

        check_pages(base, res)
        print("")
        manifest_listed_files_served(base, res)
        check_weights(base, res)
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            print("\nserver on port %d shut down" % port)

    return res.summary()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
