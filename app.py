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

# ── Google Sheets colour palette ─────────────────────────────
_STUDENT_PALETTE = [
    {"red": 0.85, "green": 0.92, "blue": 1.00},  # sky blue
    {"red": 0.88, "green": 1.00, "blue": 0.88},  # mint green
    {"red": 1.00, "green": 0.95, "blue": 0.82},  # peach
    {"red": 0.94, "green": 0.88, "blue": 1.00},  # lavender
    {"red": 1.00, "green": 0.88, "blue": 0.94},  # rose
    {"red": 0.88, "green": 1.00, "blue": 0.97},  # teal
    {"red": 1.00, "green": 1.00, "blue": 0.82},  # yellow
    {"red": 0.95, "green": 0.88, "blue": 0.82},  # sand
]
_student_color_map = {}
_color_idx = 0

def _student_color(name):
    global _color_idx
    if name not in _student_color_map:
        _student_color_map[name] = _STUDENT_PALETTE[_color_idx % len(_STUDENT_PALETTE)]
        _color_idx += 1
    return _student_color_map[name]


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
        vals = sheet.get_all_values()
        if not vals:
            sheet.append_row([
                "Time", "Student", "Sentence",
                "Score", "Passed", "Attempts", "Mastery Repetitions"
            ])
            # Style the header row
            sheet.format("A1:G1", {
                "backgroundColor": {"red": 0.13, "green": 0.08, "blue": 0.42},
                "textFormat": {
                    "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                    "bold": True, "fontSize": 11
                },
                "horizontalAlignment": "CENTER"
            })
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
        # Find the row we just wrote
        row_num = len(sheet.get_all_values())

        # Row background = student colour
        bg = _student_color(student_name)
        sheet.format(f"A{row_num}:G{row_num}", {
            "backgroundColor": bg,
            "borders": {
                "bottom": {"style": "SOLID", "color": {"red":0,"green":0,"blue":0}},
                "top":    {"style": "SOLID", "color": {"red":0,"green":0,"blue":0}},
                "left":   {"style": "SOLID", "color": {"red":0,"green":0,"blue":0}},
                "right":  {"style": "SOLID", "color": {"red":0,"green":0,"blue":0}},
            }
        })

        # Score cell colour + bold
        if score >= 85:
            score_bg = {"red": 0.72, "green": 0.96, "blue": 0.72}  # green
            score_fg = {"red": 0.05, "green": 0.30, "blue": 0.05}
        elif score >= 60:
            score_bg = {"red": 1.00, "green": 0.85, "blue": 0.55}  # orange
            score_fg = {"red": 0.40, "green": 0.20, "blue": 0.00}
        else:
            score_bg = {"red": 1.00, "green": 0.70, "blue": 0.70}  # red
            score_fg = {"red": 0.50, "green": 0.00, "blue": 0.00}

        sheet.format(f"D{row_num}", {
            "backgroundColor": score_bg,
            "textFormat": {"foregroundColor": score_fg, "bold": True}
        })
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


MAX_FAILURES = 7   # skip sentence after this many failures

COMMON_VERBS = {
    'am','is','are','was','were','have','has','had','do','does','did',
    'go','went','come','came','get','got','make','made','know','think',
    'say','said','see','saw','take','took','want','use','find','give',
    'tell','work','call','need','feel','try','leave','put','keep','run',
    'start','began','begin','write','read','speak','listen','play','help',
    'learn','study','live','move','walk','talk','meet','ask','answer',
    'understand','remember','forget','love','like','enjoy','visit',
    'travel','drive','fly','sit','stand','eat','drink','buy','sell',
}

def detect_cloze_word(sentence):
    """Return the best word to hide for cloze practice, or None."""
    words = normalize(sentence).split()
    if len(words) < 3:
        return None
    # Prefer a verb (skip first word, usually "I" or "The")
    for w in words[1:]:
        if w in COMMON_VERBS:
            return w
    # Fallback: longest non-trivial word (not first or last)
    candidates = [(len(w), w) for w in words[1:-1] if len(w) > 4]
    if candidates:
        return sorted(candidates, reverse=True)[0][1]
    return None


def new_session(name):
    return {
        "name":                name,
        "current":             0,
        "failed_attempts":     0,   # failures on current sentence
        "mastery_target":      0,   # consecutive correct needed (0 = not in mastery)
        "mastery_consecutive": 0,   # current streak
        "mastery_score":       0,   # latest passing score
        "cloze_active":        False,
        "cloze_word":          None,
        "cloze_attempts":      0,
        "sentences":           [],
        "completed":           False,
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

    def record_and_advance(final_score=None, skipped=False):
        s["sentences"].append({
            "sentence":    correct,
            "score":       final_score or s["mastery_score"] or score,
            "passed":      not skipped,
            "skipped":     skipped,
            "attempts":    s["failed_attempts"] + s["mastery_target"],
            "mastery_reps": s["mastery_target"],
        })
        log_result(
            s["name"], correct,
            final_score or s["mastery_score"] or score,
            not skipped,
            s["failed_attempts"] + s["mastery_target"],
            s["mastery_target"],
        )
        s["current"]            += 1
        s["failed_attempts"]     = 0
        s["mastery_target"]      = 0
        s["mastery_consecutive"] = 0
        s["mastery_score"]       = 0
        s["cloze_active"]        = False
        s["cloze_word"]          = None
        s["cloze_attempts"]      = 0

    def enter_cloze(first_score):
        """Activate cloze mode after first successful pronunciation."""
        cw = detect_cloze_word(correct)
        if cw:
            s["cloze_active"]   = True
            s["cloze_word"]     = cw
            s["cloze_attempts"] = 0
            s["mastery_score"]  = first_score
            return cw
        return None

    # ── Cloze mode ────────────────────────────────────────────
    if s["cloze_active"]:
        if passed:
            # Cloze passed — now enter mastery (if needed) or advance
            cw = s["cloze_word"]
            s["cloze_active"] = False
            s["cloze_word"]   = None
            if s["mastery_target"] > 0:
                # Already queued for mastery — start mastery streak
                s["mastery_consecutive"] = 1
                return jsonify({**base, "cloze_done": True,
                                "mastery_mode": True, "first_pass": True,
                                "streak_broken": False, "advance": False,
                                **mastery_payload(s)})
            else:
                record_and_advance(final_score=s["mastery_score"])
                return jsonify({**base, "cloze_done": True,
                                "mastery_mode": False, "advance": True,
                                **mastery_payload(s)})
        else:
            s["cloze_attempts"] += 1
            if s["cloze_attempts"] >= 3:
                # Too many cloze failures — proceed anyway
                cw = s["cloze_word"]
                s["cloze_active"] = False
                s["cloze_word"]   = None
                if s["mastery_target"] > 0:
                    s["mastery_consecutive"] = 0
                    return jsonify({**base, "cloze_done": True,
                                    "mastery_mode": True, "first_pass": True,
                                    "streak_broken": False, "advance": False,
                                    **mastery_payload(s)})
                else:
                    record_and_advance(final_score=s["mastery_score"])
                    return jsonify({**base, "cloze_done": True,
                                    "mastery_mode": False, "advance": True,
                                    **mastery_payload(s)})
            return jsonify({**base, "cloze_mode": True,
                            "cloze_word": s["cloze_word"],
                            "cloze_attempts_left": 3 - s["cloze_attempts"],
                            "advance": False, **mastery_payload(s)})

    # ── Mastery mode ──────────────────────────────────────────
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
            s["mastery_consecutive"] = 0
            return jsonify({**base, "mastery_mode": True,
                            "streak_broken": True, "advance": False,
                            **mastery_payload(s)})

    # ── Normal mode ───────────────────────────────────────────
    if passed:
        target = mastery_target_for(s["failed_attempts"])
        cw = enter_cloze(score)   # always try cloze first

        if target == 0:
            if cw:
                # Cloze before advancing
                return jsonify({**base, "cloze_mode": True, "cloze_word": cw,
                                "mastery_mode": False, "advance": False,
                                **mastery_payload(s)})
            # Perfect — advance directly
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
            # Queue mastery, but do cloze first
            s["mastery_target"]      = target
            s["mastery_consecutive"] = 0   # cloze counts as the warm-up
            if cw:
                return jsonify({**base, "cloze_mode": True, "cloze_word": cw,
                                "first_pass": True, "mastery_mode": False,
                                "advance": False, **mastery_payload(s)})
            # No cloze word — enter mastery directly
            s["mastery_consecutive"] = 1
            s["mastery_score"]       = score
            if s["mastery_consecutive"] >= target:
                record_and_advance()
                return jsonify({**base, "mastery_mode": False,
                                "advance": True, **mastery_payload(s)})
            return jsonify({**base, "mastery_mode": True,
                            "first_pass": True, "streak_broken": False,
                            "advance": False, **mastery_payload(s)})
    else:
        s["failed_attempts"] += 1
        if s["failed_attempts"] >= MAX_FAILURES:
            # Skip this sentence
            record_and_advance(final_score=score, skipped=True)
            return jsonify({**base, "skipped": True, "advance": True,
                            "mastery_mode": False, **mastery_payload(s)})
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


@app.route("/score-only", methods=["POST"])
def score_only():
    """Exam mode: score without affecting session state."""
    data    = request.get_json(force=True)
    spoken  = data.get("spoken", "")
    correct = data.get("correct", "")
    score   = similarity(spoken, correct)
    passed  = score >= PASS_THRESHOLD
    words   = word_level(spoken, correct)
    return jsonify(score=score, passed=passed, words=words, threshold=PASS_THRESHOLD)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
