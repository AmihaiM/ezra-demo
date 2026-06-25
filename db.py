"""
db.py — EZRA database layer
Supabase when USE_DB=true, falls back to questions.json otherwise.
"""

import os
import json
import time
import uuid
from pathlib import Path

USE_DB = os.environ.get("USE_DB", "false").lower() == "true"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://hoynzkiefvcyuiwixgye.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # set SUPABASE_KEY env var on Render

_supabase = None

def get_client():
    global _supabase
    if _supabase is None:
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


# ──────────────────────────────────────────────
# EXERCISES
# ──────────────────────────────────────────────

def load_exercises(class_id: str = None, lesson_id: str = None) -> list[dict]:
    """
    Returns list of exercises: [{"id": ..., "he": ..., "en": ...}, ...]
    Falls back to questions.json when USE_DB is false.
    """
    if not USE_DB:
        return _load_json_fallback()

    db = get_client()
    query = db.table("exercises").select(
        "id, he_text, en_text, difficulty, lesson_id"
    ).eq("approved", True)

    if lesson_id:
        query = query.eq("lesson_id", lesson_id)
    elif class_id:
        # join through lessons
        query = db.table("exercises").select(
            "id, he_text, en_text, difficulty, lesson_id, lessons!inner(class_id)"
        ).eq("approved", True).eq("lessons.class_id", class_id)

    res = query.execute()
    return [
        {"id": row["id"], "he": row["he_text"], "en": row["en_text"]}
        for row in (res.data or [])
    ]


def _load_json_fallback() -> list[dict]:
    """Original JSON-based questions for the demo."""
    json_path = Path(__file__).parent / "questions.json"
    if not json_path.exists():
        return []
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    # support both flat list and {questions: [...]} shapes
    if isinstance(data, list):
        return data
    return data.get("questions", [])


# ──────────────────────────────────────────────
# SESSIONS
# ──────────────────────────────────────────────

def create_session(student_id: str, lesson_id: str = None, mode: str = "practice") -> str:
    """Creates a practice session row, returns session_id."""
    if not USE_DB:
        return str(uuid.uuid4())  # dummy ID when not using DB

    db = get_client()
    res = db.table("sessions").insert({
        "student_id": student_id,
        "lesson_id": lesson_id,
        "mode": mode,
    }).execute()
    return res.data[0]["id"]


def close_session(session_id: str, total: int, mastery: int):
    """Marks a session as ended."""
    if not USE_DB:
        return

    import datetime
    db = get_client()
    db.table("sessions").update({
        "ended_at": datetime.datetime.utcnow().isoformat(),
        "total_exercises": total,
        "mastery_count": mastery,
    }).eq("id", session_id).execute()


# ──────────────────────────────────────────────
# ATTEMPTS
# ──────────────────────────────────────────────

def log_attempt(
    student_id: str,
    exercise_id: str,
    spoken_text: str,
    score: float,
    passed: bool,
    attempt_number: int,
    mastery_required: int,
    mastery_completed: int,
    time_to_speak: float = None,
    session_id: str = None,
    error_tags: dict = None,
) -> str | None:
    """
    Logs one attempt to Supabase.
    Returns attempt id, or None if using fallback.
    """
    if not USE_DB:
        return None

    db = get_client()
    res = db.table("attempts").insert({
        "session_id": session_id,
        "student_id": student_id,
        "exercise_id": exercise_id,
        "spoken_text": spoken_text,
        "score": score,
        "passed": passed,
        "attempt_number": attempt_number,
        "mastery_required": mastery_required,
        "mastery_completed": mastery_completed,
        "time_to_speak": time_to_speak,
        "error_tags": error_tags or {},
    }).execute()
    return res.data[0]["id"] if res.data else None


# ──────────────────────────────────────────────
# ANALYTICS
# ──────────────────────────────────────────────

def get_student_progress(student_id: str) -> dict:
    """Returns summary stats for one student."""
    if not USE_DB:
        return {}

    db = get_client()
    res = db.table("student_progress").select("*").eq("student_id", student_id).execute()
    return res.data[0] if res.data else {}


def get_class_progress(class_id: str) -> list[dict]:
    """Returns progress rows for all students in a class."""
    if not USE_DB:
        return []

    db = get_client()
    res = db.table("student_progress").select("*").eq("class_id", class_id).execute()
    return res.data or []


def get_class_error_patterns(class_id: str) -> list[dict]:
    """
    Returns most common error tags across a class.
    Useful for: 'כל הכיתה מתקשה עם past tense'
    """
    if not USE_DB:
        return []

    db = get_client()
    # raw SQL via rpc or just pull and aggregate in Python
    res = db.table("attempts").select(
        "error_tags, students!inner(class_id)"
    ).eq("students.class_id", class_id).execute()

    tag_counts: dict = {}
    for row in (res.data or []):
        tags = row.get("error_tags") or {}
        for category, words in tags.items():
            for w in (words or []):
                key = f"{category}:{w}"
                tag_counts[key] = tag_counts.get(key, 0) + 1

    return sorted(
        [{"tag": k, "count": v} for k, v in tag_counts.items()],
        key=lambda x: -x["count"]
    )
