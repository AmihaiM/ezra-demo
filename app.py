from flask import Flask, jsonify, request, make_response
from difflib import SequenceMatcher
import json, re, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

with open(os.path.join(BASE_DIR, "questions.json"), encoding="utf-8") as f:
    QUESTIONS = json.load(f)

current = 0
session_results = []
PASS_THRESHOLD = 75   # % similarity to pass


def normalize(text):
    """Lowercase, strip punctuation."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def similarity(a, b):
    """Overall string similarity 0-100."""
    return int(SequenceMatcher(None, normalize(a), normalize(b)).ratio() * 100)


def word_level(spoken, correct):
    """
    Returns list of {word, status} for colour-coded feedback.
    status: 'correct' | 'wrong' | 'missing'
    """
    sp_words = normalize(spoken).split()
    co_words = normalize(correct).split()
    matcher  = SequenceMatcher(None, sp_words, co_words)
    result   = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for w in co_words[j1:j2]:
                result.append({"word": w, "status": "correct"})
        elif tag in ("replace", "delete"):
            for w in co_words[j1:j2]:
                result.append({"word": w, "status": "wrong"})
        elif tag == "insert":
            for w in co_words[j1:j2]:
                result.append({"word": w, "status": "missing"})
    return result


@app.route("/question")
def get_question():
    global current
    if current >= len(QUESTIONS):
        total   = len(session_results)
        passed  = sum(1 for r in session_results if r["passed"])
        avg     = int(sum(r["score"] for r in session_results) / total) if total else 0
        return jsonify({"done": True, "results": session_results,
                        "passed": passed, "total": total, "avg_score": avg})
    q = QUESTIONS[current].copy()
    q["index"]  = current
    q["total"]  = len(QUESTIONS)
    return jsonify(q)


@app.route("/answer", methods=["POST"])
def post_answer():
    global current
    data      = request.get_json(force=True)
    spoken    = data.get("answer", "")
    correct   = QUESTIONS[current]["en"]
    score     = similarity(spoken, correct)
    passed    = score >= PASS_THRESHOLD
    words     = word_level(spoken, correct)

    session_results.append({
        "sentence": correct,
        "spoken":   spoken,
        "score":    score,
        "passed":   passed,
    })
    if passed:
        current += 1

    return jsonify({
        "score":   score,
        "passed":  passed,
        "correct": correct,
        "spoken":  spoken,
        "words":   words,        # word-level colour feedback
        "threshold": PASS_THRESHOLD,
    })


@app.route("/reset", methods=["POST"])
def reset():
    global current, session_results
    current = 0
    session_results = []
    return jsonify({"ok": True})


@app.route("/")
def home():
    r = make_response(open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read())
    r.headers["Content-Type"] = "text/html; charset=utf-8"
    return r


if __name__ == "__main__":
    print("\n  EZRA PoC  →  http://localhost:5000\n")
    app.run(debug=True)
