from flask import Flask, jsonify, request, make_response
from difflib import SequenceMatcher
import json, re, os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

with open(os.path.join(BASE_DIR, "questions.json"), encoding="utf-8") as f:
    QUESTIONS = json.load(f)

PASS_THRESHOLD = 85
students = {}


# ── Google Sheets logging ────────────────────────────────────
def _get_sheet():
    """Return the first sheet of the configured Google Spreadsheet, or None."""
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
    """Add header row if the sheet is empty."""
    try:
        if not sheet.get_all_values():
            sheet.append_row([
                "Timestamp", "Student", "Sentence",
                "Score", "Passed", "Attempts", "Mastery Reps"
            ])
    except Exception:
        pass


def log_result(student_name, sentence, score, passed, attempts, mastery_reps):
    """Write one completed sentence to Google Sheets (silently fails if not configured)."""
    try:
        sheet = _get_sheet()
        if sheet is None:
            return
        _ensure_header(sheet)
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            student_name,
            sentence,
            score,
            "Yes" if passed else "No",
            attempts,
            mastery_reps,
        ])
    except Exception:
        pass   # Never let logging break the app


# ── Session helpers ──────────────────────────────────────────
def new_session(name):
    return {
        "name":            name,
        "current":         0,
        "failed_attempts": 0,
        "mastery_needed":  0,
        "mastery_score":   0,
        "sentences":       [],
        "completed":       False,
    }


def get_session(sid):
    if sid not in students:
        students[sid] = new_session(sid)
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
    q.update(index=s["current"], total=len(QUESTIONS),
             mastery_needed=s["mastery_needed"],
             failed_attempts=s["failed_attempts"])
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

    def record_and_advance(attempts, mastery_reps, final_score):
        """Save sentence, log to Sheets, advance counter."""
        rec = {"sentence": correct, "score": final_score,
               "passed": True, "attempts": attempts, "mastery_reps": mastery_reps}
        s["sentences"].append(rec)
        log_result(s["name"], correct, final_score, True, attempts, mastery_reps)
        s["current"]        += 1
        s["failed_attempts"] = 0
        s["mastery_score"]   = 0

    # ── Mastery mode ─────────────────────────────────────────
    if s["mastery_needed"] > 0:
        if passed:
            s["mastery_needed"] -= 1
            if s["mastery_needed"] == 0:
                record_and_advance(
                    attempts     = s["failed_attempts"] + 1,
                    mastery_reps = s["failed_attempts"],
                    final_score  = s["mastery_score"],
                )
                return jsonify({**base, "mastery_mode": False,
                                "mastery_needed": 0, "advance": True})
        return jsonify({**base, "mastery_mode": True,
                        "mastery_needed": s["mastery_needed"], "advance": False})

    # ── Normal mode ───────────────────────────────────────────
    if passed:
        if s["failed_attempts"] == 0:
            record_and_advance(attempts=1, mastery_reps=0, final_score=score)
            return jsonify({**base, "mastery_mode": False,
                            "mastery_needed": 0, "advance": True})
        else:
            s["mastery_needed"] = s["failed_attempts"]
            s["mastery_score"]  = score
            return jsonify({**base, "mastery_mode": True, "first_pass": True,
                            "mastery_needed": s["mastery_needed"], "advance": False})
    else:
        s["failed_attempts"] += 1
        return jsonify({**base, "mastery_mode": False, "mastery_needed": 0,
                        "advance": False, "failed_attempts": s["failed_attempts"]})


@app.route("/reset", methods=["POST"])
def reset():
    data = request.get_json(force=True)
    sid  = data.get("student", "guest")
    students[sid] = new_session(sid)
    return jsonify({"ok": True})


@app.route("/teacher")
def teacher():
    path = os.path.join(BASE_DIR, "teacher.html")
    r = make_response(open(path, encoding="utf-8").read())
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    return r


@app.route("/teacher/data")
def teacher_data():
    out = []
    for sid, s in students.items():
        sents  = s["sentences"]
        avg    = int(sum(r["score"] for r in sents) / len(sents)) if sents else 0
        passed = sum(1 for r in sents if r["passed"])
        out.append({
            "name":           s["name"],
            "current":        s["current"],
            "total":          len(QUESTIONS),
            "completed":      s["completed"],
            "avg_score":      avg,
            "passed":         passed,
            "sentences":      sents,
            "mastery_needed": s["mastery_needed"],
            "failed_current": s["failed_attempts"],
        })
    return jsonify(out)


@app.route("/")
def home():
    r = make_response(open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read())
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    return r


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
