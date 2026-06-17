from flask import Flask, jsonify, request, make_response, session, redirect
from difflib import SequenceMatcher
import json, re, os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-fallback-key")

with open(os.path.join(BASE_DIR, "questions.json"), encoding="utf-8") as f:
    QUESTIONS = json.load(f)

PASS_THRESHOLD = 85
students = {}


# ── Google Sheets logging ────────────────────────────────────
def _get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    sheet_id   = os.environ.get("GOOGLE_SHEET_ID")
    if not creds_json or not sheet_id:
        return None
    try:
        import gspread
        creds = json.loads(creds_json)
        gc    = gspread.service_account_from_dict(creds)
        return gc.open_by_key(sheet_id).sheet1
    except Exception:
        return None


def _ensure_header(sheet):
    try:
        if not sheet.get_all_values():
            sheet.append_row([
                "Time", "Student", "Sentence",
                "Score", "Passed", "Attempts", "Mastery Repetitions"
            ])
    except Exception:
        pass


def log_result(student_name, sentence, score, passed, attempts, mastery_reps):
    try:
        sheet = _get_sheet()
        if sheet is None:
            return
        _ensure_header(sheet)
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            student_name, sentence, score,
            "Yes" if passed else "No",
            attempts, mastery_reps,
        ])
    except Exception:
        pass


# ── Session helpers ──────────────────────────────────────────
def mastery_target_for(failures):
    """
    Consecutive correct productions needed, per Bloom / motor-language research:
      0 failures → 0  (advance immediately)
      1 failure  → 3
      2 failures → 4
      3+         → 5
    """
    if failures == 0:
        return 0
    return min(failures + 2, 5)


def new_session(name):
    return {
        "name":               name,
        "current":            0,
        "failed_attempts":    0,   # failures on current sentence
        "mastery_target":     0,   # consecutive correct needed (0 = not in mastery)
        "mastery_consecutive": 0,  # current streak
        "mastery_score":      0,   # latest passing score
        "sentences":          [],
        "completed":          False,
    }


def get_session(sid):
    if sid not in students:
        name = sid.rsplit("_", 1)[0] if "_" in sid else sid
        students[sid] = new_session(name)
    return students[sid]


def normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def similarity(a, b):
    return int(SequenceMatcher(None, normalize(a), normalize(b)).ratio() * 100)


def word_level(spoken, correct):
    sp = normalize(spoken).split()
    co = normalize(correct).split()
    result = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, sp, co).get_opcodes():
        if tag == "equal":
            for w in co[j1:j2]: result.append({"word": w, "status": "correct"})
        elif tag in ("replace", "delete"):
            for w in co[j1:j2]: result.append({"word": w, "status": "wrong"})
        elif tag == "insert":
            for w in co[j1:j2]: result.append({"word": w, "status": "missing"})
    return result


def mastery_payload(s):
    return {
        "mastery_target":      s["mastery_target"],
        "mastery_consecutive": s["mastery_consecutive"],
        "mastery_remaining":   s["mastery_target"] - s["mastery_consecutive"],
    }


# ── Routes ───────────────────────────────────────────────────
@app.route("/question")
def get_question():
    sid = request.args.get("student", "guest")
    s   = get_session(sid)

    if s["current"] >= len(QUESTIONS):
        s["completed"] = True
        sents  = s["sentences"]
        total  = len(sents)
        passed = sum(1 for r in sents if r["passed"])
        avg    = int(sum(r["score"] for r in sents) / total) if total else 0
        return jsonify({"done": True, "results": sents,
                        "passed": passed, "total": total, "avg_score": avg})

    q = QUESTIONS[s["current"]].copy()
    q.update(
        index              = s["current"],
        total              = len(QUESTIONS),
        failed_attempts    = s["failed_attempts"],
        **mastery_payload(s)
    )
    return jsonify(q)


@app.route("/answer", methods=["POST"])
def post_answer():
    data   = request.get_json(force=True)
    sid    = data.get("student", "guest")
    spoken = data.get("answer", "")
    s      = get_session(sid)

    correct = QUESTIONS[s["current"]]["en"]
    score   = similarity(spoken, correct)
    passed  = score >= PASS_THRESHOLD
    words   = word_level(spoken, correct)

    base = dict(correct=correct, spoken=spoken, words=words,
                threshold=PASS_THRESHOLD, score=score, passed=passed)

    def record_and_advance():
        s["sentences"].append({
            "sentence":    correct,
            "score":       s["mastery_score"] or score,
            "passed":      True,
            "attempts":    s["failed_attempts"] + s["mastery_target"],
            "mastery_reps": s["mastery_target"],
        })
        log_result(
            s["name"], correct,
            s["mastery_score"] or score, True,
            s["failed_attempts"] + s["mastery_target"],
            s["mastery_target"],
        )
        s["current"]            += 1
        s["failed_attempts"]     = 0
        s["mastery_target"]      = 0
        s["mastery_consecutive"] = 0
        s["mastery_score"]       = 0

    # ── In mastery mode ───────────────────────────────────────
    if s["mastery_target"] > 0:
        if passed:
            s["mastery_consecutive"] += 1
            s["mastery_score"]        = score

            if s["mastery_consecutive"] >= s["mastery_target"]:
                record_and_advance()
                return jsonify({**base, "mastery_mode": False,
                                "mastery_mode_done": True,
                                "advance": True, **mastery_payload(s)})

            return jsonify({**base, "mastery_mode": True,
                            "streak_broken": False, "advance": False,
                            **mastery_payload(s)})
        else:
            # Streak broken — reset consecutive
            s["mastery_consecutive"] = 0
            return jsonify({**base, "mastery_mode": True,
                            "streak_broken": True, "advance": False,
                            **mastery_payload(s)})

    # ── Normal mode ───────────────────────────────────────────
    if passed:
        target = mastery_target_for(s["failed_attempts"])
        if target == 0:
            # Perfect first attempt — advance directly
            s["sentences"].append({
                "sentence": correct, "score": score,
                "passed": True, "attempts": 1, "mastery_reps": 0,
            })
            log_result(s["name"], correct, score, True, 1, 0)
            s["current"]        += 1
            s["failed_attempts"] = 0
            return jsonify({**base, "mastery_mode": False,
                            "advance": True, **mastery_payload(s)})
        else:
            # Enter mastery: first pass counts as consecutive #1
            s["mastery_target"]      = target
            s["mastery_consecutive"] = 1
            s["mastery_score"]       = score

            if s["mastery_consecutive"] >= target:   # edge case: target=1
                record_and_advance()
                return jsonify({**base, "mastery_mode": False,
                                "advance": True, **mastery_payload(s)})

            return jsonify({**base, "mastery_mode": True,
                            "first_pass": True, "streak_broken": False,
                            "advance": False, **mastery_payload(s)})
    else:
        s["failed_attempts"] += 1
        return jsonify({**base, "mastery_mode": False, "advance": False,
                        "failed_attempts": s["failed_attempts"],
                        **mastery_payload(s)})


@app.route("/reset", methods=["POST"])
def reset():
    data = request.get_json(force=True)
    sid  = data.get("student", "guest")
    name = sid.rsplit("_", 1)[0] if "_" in sid else sid
    students[sid] = new_session(name)
    return jsonify({"ok": True})


@app.route("/")
def home():
    r = make_response(open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read())
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    return r


# ── Auth: student password check ─────────────────────────────
@app.route("/verify-student", methods=["POST"])
def verify_student():
    data     = request.get_json(force=True)
    password = data.get("password", "")
    expected = os.environ.get("STUDENT_PASSWORD", "")
    if not expected or password == expected:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


# ── Auth: teacher login / logout ──────────────────────────────
@app.route("/teacher-login", methods=["GET", "POST"])
def teacher_login():
    error = ""
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        expected = os.environ.get("TEACHER_PASSWORD", "")
        if expected and password == expected and "@" in email:
            session["teacher"] = email
            return redirect("/teacher")
        error = "אימייל או סיסמה שגויים"

    path = os.path.join(BASE_DIR, "teacher_login.html")
    html = open(path, encoding="utf-8").read().replace("{{ERROR}}", error)
    r = make_response(html)
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    return r


@app.route("/teacher-logout")
def teacher_logout():
    session.pop("teacher", None)
    return redirect("/teacher-login")


@app.route("/teacher")
def teacher():
    if not session.get("teacher"):
        return redirect("/teacher-login")
    path = os.path.join(BASE_DIR, "teacher.html")
    r = make_response(open(path, encoding="utf-8").read())
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    return r


@app.route("/teacher/data")
def teacher_data():
    if not session.get("teacher"):
        return jsonify({"error": "unauthorized"}), 401
    out = []
    for sid, s in students.items():
        sents  = s["sentences"]
        avg    = int(sum(r["score"] for r in sents) / len(sents)) if sents else 0
        passed = sum(1 for r in sents if r["passed"])
        out.append({
            "name":               s["name"],
            "current":            s["current"],
            "total":              len(QUESTIONS),
            "completed":          s["completed"],
            "avg_score":          avg,
            "passed":             passed,
            "sentences":          sents,
            "mastery_target":     s["mastery_target"],
            "mastery_consecutive": s["mastery_consecutive"],
            "failed_current":     s["failed_attempts"],
        })
    return jsonify(out)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
