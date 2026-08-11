"""Writing `weights/` artifacts so the browser can actually read them.

THREE THINGS BIT US, AND EACH IS FIXED HERE ONCE
------------------------------------------------
1. BARE NaN IS NOT JSON.
   Python's `json.dump` emits `NaN` / `Infinity` by default. Python reads them
   back fine, so the file looks healthy from this side -- but `JSON.parse` and
   `fetch().json()` reject the WHOLE FILE. `reinforce.json` carried 2,829 bare
   NaNs (REINFORCE has no critic, so its `critic_mse` is legitimately NaN) and
   was unreadable in the browser while looking perfect in Python.
   `dump_json` below scrubs non-finite floats to null AND passes
   `allow_nan=False`, so a future leak raises here instead of shipping.

2. `file://` CANNOT fetch().
   The user must be able to double-click the HTML. `write_bundle` emits a
   plain `.js` that assigns everything to `window`, which a `<script src>` tag
   loads with no CORS involvement.

3. THE STRATEGY LIST WAS HARD-CODED IN TWO PLACES.
   `write_index` emits `weights/index.json` so the web loader reads the roster
   instead of guessing filenames, and untrained strategies are announced as
   untrained rather than silently missing.

`parity_verified` is stamped here too. It is not a decoration: no artifact
should claim a number until `parity_fastsim.py` says the simulator that
produced it agrees with the engine.
"""

from __future__ import annotations

import json
import math
import os

from critic_roles import (CAP_NOTE_KO, CRITIC_ROLES_KO, MEDIAN_NOTE_KO,
                           SCORE_METRIC_NOTE_KO)

WEIGHTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "weights")


def scrub(o):
    """Replace every non-finite float with None, recursively."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [scrub(v) for v in o]
    return o


def dump_json(path, payload, indent=1):
    """The only way this package writes JSON. Browser-safe by construction."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        # allow_nan=False turns a future leak into an exception right here.
        json.dump(scrub(payload), f, indent=indent, allow_nan=False)
    return path


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

# id, panel, file, Korean label, kind, critic role (short), study-plan number
ROSTER = [
    ("random",             1, None,                      "Random",
     None,           "없음 (대조군)",                     None),
    ("dellacherie",        2, None,                      "Dellacherie 수동 가중치",
     "linear",       "없음 — 사람이 정한 평가함수",        None),
    ("cem_linear",         3, "cem_linear.json",         "CEM 선형",
     "linear",       "없음 — 진화전략이 가중치를 직접 탐색", None),
    ("reinforce",          4, "reinforce.json",          "REINFORCE",
     "policy_mlp",   "없음 · 실제 리턴 G_t",              1),
    ("reinforce_baseline", 5, "reinforce_baseline.json", "REINFORCE + baseline",
     "policy_mlp",   "분산 감소 기준선 (부트스트랩 아님)",  2),
    ("a2c",                6, "a2c.json",                "A2C (1-step)",
     "policy_mlp",   "부트스트랩 표적 v(s_{t+1})",         3),
    ("dqn",                7, "dqn.json",                "DQN (afterstate)",
     "mlp",          "Q가 정책 그 자체 (argmax)",          4),
    ("search_1ply",        8, "search_1ply.json",        "1-ply 탐색 + 학습된 가치",
     "linear",       "탐색이 정책 개선, 가치망은 말단 평가", None),
    # Panel 9 is the same algorithm as panel 3 with a different objective.
    # Side by side they show that the objective, not the method, decides the
    # style of play: panel 3 shaves single lines forever, panel 9 digs a well
    # and waits for the I-piece.
    ("cem_score",          9, "cem_score.json",          "CEM 점수 목표 · 특징 8종 (15분)",
     "linear",       "없음 — 목표만 줄 수에서 점수로 바꿈",  None),
    # Panel 10 is the SUPERSEDED search rule, kept so the comparison that
    # justified changing it stays reproducible. Not for the UI's main grid.
    ("search_1ply_sum",   10, "search_1ply_sum.json",    "1-ply 탐색 (구 합산 규칙, 폐기)",
     "linear",       "탐색이 정책 개선 — 단 평가 규칙이 틀렸다", None),
    # Budget/feature variants of panel 9. Registered so they are stamped and
    # visible; the UI's main grid does not need them.
    ("cem_score_long",    11, "cem_score_long.json",     "CEM 점수 목표 · 특징 8종 (55분)",
     "linear",       "없음 — 패널 9와 예산만 다르다",        None),
    ("cem_score_wells",   12, "cem_score_wells.json",    "CEM 점수 목표 · 우물 특징 추가 (55분)",
     "linear",       "없음 — 패널 11과 특징 두 개만 다르다",  None),
    # Panel 13 removes the confounder in panel 8: these weights were fitted
    # WITH the 2-ply search in the rollout, so the value function was actually
    # asked to be good at the depth it is used at.
    ("cem_search",        13, "cem_search.json",         "CEM (탐색을 넣고 학습)",
     "linear",       "탐색이 정책 개선 — 가치도 그 깊이에 맞춰 학습", None),
    # Panels 14/15 change ONE thing against panel 12: the policy is a net
    # instead of a linear function. Same objective, same features, same budget.
    ("cem_score_wells_mlp8",  14, "cem_score_wells_mlp8.json",
     "CEM 점수 목표 · 우물 특징 · MLP 은닉8 (55분)",
     "mlp",          "없음 — 패널 12와 정책 형태만 다르다",  None),
    ("cem_score_wells_mlp4",  15, "cem_score_wells_mlp4.json",
     "CEM 점수 목표 · 우물 특징 · MLP 은닉4 (15분)",
     "mlp",          "없음 — 파라미터 수를 줄인 대조",       None),
    # Panel 16 changes ONE thing against panel 12: the fitness multiplies the
    # score by how much of the piece budget the run survived. Same features,
    # same budget, same linear policy.
    ("cem_score_safe",    16, "cem_score_safe.json",
     "CEM 점수+생존 목표 · 우물 특징 (55분)",
     "linear",       "없음 — 패널 12와 적합도만 다르다",     None),
]


def write_index(weights_dir=WEIGHTS_DIR):
    """weights/index.json -- the roster the web loader reads."""
    strategies = []
    for sid, panel, fname, label, kind, critic, plan in ROSTER:
        entry = {
            "id": sid,
            "panel": panel,
            "file": fname,
            "label": label,
            "kind": kind,
            "critic_role_short": critic,
            "study_plan_number": plan,
            # Only this panel must run 2-ply lookahead. Run it greedily and it
            # reproduces panel 3 exactly, because the weights are identical.
            "needs_search": sid == "search_1ply",
            # Dellacherie's constants live in rl/features.py; there is no file.
            "weights_in_code": sid == "dellacherie",
            "trained": False,
            "median_lines": None,
            "mean_lines": None,
            "parity_verified": None,
        }
        ko = CRITIC_ROLES_KO.get(sid)
        if ko:
            entry["critic_role_ko"] = ko["body"]
            entry["critic_role_ko_headline"] = ko["headline"]
            entry["update_formula"] = ko["formula"]
        # NEVER name a file that is not on disk. The app treats this manifest as
        # its shopping list and fetches what it names, so a listed-but-missing
        # file 404s and that panel silently does not run -- it reads as "not
        # trained yet", not as an error. Registering a roster slot before the
        # training that fills it produced exactly that. Unbuilt variants keep
        # their slot with file=null, which is the same shape `random` uses.
        if fname:
            path = os.path.join(weights_dir, fname)
            if not os.path.exists(path):
                entry["file"] = None
                entry["not_built_yet"] = True
            else:
                with open(path) as f:
                    d = json.load(f)
                meta = d.get("meta", {})
                entry["trained"] = True
                ev = meta.get("eval", {})
                entry["mean_lines"] = ev.get("mean_lines")
                entry["median_lines"] = ev.get("median_lines")
                entry["parity_verified"] = meta.get("parity_verified")
        strategies.append(entry)

    payload = {
        "note": "rl 이 유지한다. web 은 파일명을 하드코딩하지 말고 이 목록을 읽는다. "
                "trained=false 는 학습 전이거나 파일이 없다는 뜻이며, UI 는 그것을 "
                "정직하게 표시하고 성적인 척하지 않는다.",
        "feature_order": [
            "landing_height", "eroded_piece_cells", "row_transitions",
            "column_transitions", "holes", "cumulative_wells",
            "aggregate_height", "bumpiness"],
        "landing_height_formula":
            "mean(22 - y for (y, x) in info.piece_cells)  # 바닥 y=21 의 높이는 1",
        "feature_order_note":
            "가중치 파일의 값은 그 파일의 `features` 목록 순서를 따른다. "
            "인덱스로 읽지 말고 이름으로 정렬해라 — 특징 세트가 늘어나도 "
            "옛 파일이 안 깨진다.",
        "notes_ko": {
            "cap": CAP_NOTE_KO,
            "median": MEDIAN_NOTE_KO,
            "score_metric": SCORE_METRIC_NOTE_KO,
        },
        "strategies": strategies,
    }
    return dump_json(os.path.join(weights_dir, "index.json"), payload)


def write_bundle(weights_dir=WEIGHTS_DIR):
    """weights/weights_bundle.js -- everything on `window`, for file:// use."""
    models = {}
    for sid, _p, fname, *_rest in ROSTER:
        if not fname:
            continue
        path = os.path.join(weights_dir, fname)
        if os.path.exists(path):
            with open(path) as f:
                models[sid] = json.load(f)

    index_path = os.path.join(weights_dir, "index.json")
    index = json.load(open(index_path)) if os.path.exists(index_path) else None
    summary_path = os.path.join(weights_dir, "eval_summary.json")
    summary = json.load(open(summary_path)) if os.path.exists(summary_path) else None

    out = os.path.join(weights_dir, "weights_bundle.js")
    with open(out, "w") as f:
        f.write("// Generated by rl/artifacts.py -- do not edit.\n")
        f.write("// Lets index.html run from file:// where fetch() is blocked by CORS.\n")
        f.write("window.TETRIS_WEIGHTS = ")
        json.dump(scrub(models), f, allow_nan=False)
        f.write(";\nwindow.TETRIS_WEIGHTS_INDEX = ")
        json.dump(scrub(index), f, allow_nan=False)
        f.write(";\nwindow.TETRIS_EVAL_SUMMARY = ")
        json.dump(scrub(summary), f, allow_nan=False)
        f.write(";\n")
    return out


def stamp_parity(verified: bool, weights_dir=WEIGHTS_DIR):
    """Set meta.parity_verified and the Korean critic text on every weights file.

    Also repairs any file that already shipped with bare NaN.

    The Korean text is injected here rather than written by each trainer so that
    there is exactly one place to edit it. `web` renders `meta.critic_role_ko`
    verbatim -- it must never translate, because the wording is matched to the
    user's own study document and a paraphrase would break that alignment.
    """
    # SCAN THE DIRECTORY, DO NOT ITERATE THE ROSTER.
    # Iterating the roster was the actual bug behind three separate "why is
    # parity_verified null again" reports: every new training variant
    # (cem_score, then cem_score_long) landed as a file the roster did not
    # know about, so it was skipped and shipped with null provenance. Fixing
    # the files one at a time could never converge -- a policy file is
    # anything with a `kind`, and all of them get stamped.
    by_name = {fname: sid for sid, _p, fname, *_r in ROSTER if fname}
    touched = []
    for fname in sorted(os.listdir(weights_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(weights_dir, fname)
        try:
            with open(path) as f:
                probe = json.load(f)
        except (ValueError, OSError):
            continue
        # A policy weights file is one that declares how to run it. Summaries
        # (index.json, eval_summary.json) and checkpoints have no `kind`.
        if not isinstance(probe, dict) or "kind" not in probe:
            continue
        sid = by_name.get(fname, probe.get("name", ""))
        with open(path) as f:
            d = json.load(f)          # Python tolerates the NaN we are removing
        meta = d.setdefault("meta", {})
        meta["parity_verified"] = verified
        # Variants share their base strategy's explanation (cem_score_long is
        # cem_score with a longer budget, not a different idea).
        ko = CRITIC_ROLES_KO.get(sid) or CRITIC_ROLES_KO.get(
            sid.rsplit("_", 1)[0] if "_" in sid else sid)
        if ko:
            meta["critic_role_ko"] = ko["body"]
            meta["critic_role_ko_headline"] = ko["headline"]
            meta["update_formula"] = ko["formula"]
        dump_json(path, d)
        touched.append(fname)
    return touched


def sync_eval_from_summary(weights_dir=WEIGHTS_DIR):
    """Copy the engine-measured result into each weights file's `meta.eval`.

    Trainers write `meta.eval` from their own in-training check, which runs on
    `fastsim` and reports only means. The authoritative table is
    `eval_summary.json` -- engine-measured, median-first, with per_game -- and
    the two drifting apart is how a file ends up advertising a number the
    comparison table does not contain.

    The trainer's own figures are preserved as `meta.eval_training` rather than
    discarded: the gap between an in-training check and a held-out engine run is
    itself worth being able to look at.
    """
    path = os.path.join(weights_dir, "eval_summary.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        summary = json.load(f).get("strategies", {})

    touched = []
    for sid, rec in summary.items():
        fname = next((f for i, _p, f, *_r in ROSTER if i == sid and f), None)
        if not fname:
            continue
        wpath = os.path.join(weights_dir, fname)
        if not os.path.exists(wpath):
            continue
        with open(wpath) as f:
            d = json.load(f)
        meta = d.setdefault("meta", {})
        if "eval" in meta and "eval_training" not in meta:
            meta["eval_training"] = meta["eval"]
        meta["eval"] = {k: v for k, v in rec.items()
                        if k not in ("panel", "label", "critic_role")}
        meta["eval"]["source"] = "evaluate.py (engine)"
        dump_json(wpath, d)
        touched.append(fname)
    return touched


def rebuild_all(verified: bool):
    sync_eval_from_summary()          # before the index, which reads meta.eval
    touched = stamp_parity(verified)
    idx = write_index()
    bundle = write_bundle()
    return touched, idx, bundle


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--parity", choices=["true", "false", "run"], default="run",
                    help="'run' executes parity_fastsim and stamps the result")
    args = ap.parse_args()

    if args.parity == "run":
        import subprocess
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        r = subprocess.run([sys.executable, os.path.join(here, "parity_fastsim.py"),
                            "--games", "40", "--pieces", "400"],
                           capture_output=True, text=True, cwd=here)
        print(r.stdout.strip().splitlines()[-1])
        verified = (r.returncode == 0)
    else:
        verified = args.parity == "true"

    touched, idx, bundle = rebuild_all(verified)
    print(f"parity_verified = {verified}")
    print(f"stamped + NaN-scrubbed: {', '.join(touched)}")
    print(f"wrote {idx}")
    print(f"wrote {bundle}")
