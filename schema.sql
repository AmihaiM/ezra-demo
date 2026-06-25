-- ============================================================
-- EZRA — Supabase Schema
-- Project: hoynzkiefvcyuiwixgye
-- Run this in: Supabase Dashboard → SQL Editor → New query
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 1. PROFILES  (extends Supabase auth.users)
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
  id        uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role      text NOT NULL CHECK (role IN ('teacher', 'student', 'admin')),
  name      text NOT NULL,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users can read own profile"
  ON profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY "Users can update own profile"
  ON profiles FOR UPDATE USING (auth.uid() = id);


-- ────────────────────────────────────────────────────────────
-- 2. CLASSES
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS classes (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name       text NOT NULL,
  grade      text,
  subject    text DEFAULT 'English',
  created_at timestamptz DEFAULT now()
);

ALTER TABLE classes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Teachers can manage own classes"
  ON classes FOR ALL USING (teacher_id = auth.uid());
CREATE POLICY "Students can view their class"
  ON classes FOR SELECT
  USING (id IN (SELECT class_id FROM students WHERE auth_id = auth.uid()));


-- ────────────────────────────────────────────────────────────
-- 3. STUDENTS
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  class_id        uuid REFERENCES classes(id) ON DELETE SET NULL,
  auth_id         uuid REFERENCES auth.users(id) ON DELETE SET NULL,  -- optional: for self-registered students
  display_name    text NOT NULL,
  level           integer DEFAULT 1 CHECK (level BETWEEN 1 AND 5),
  placement_score numeric(5,2),
  created_at      timestamptz DEFAULT now()
);

ALTER TABLE students ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Teachers can manage students in own classes"
  ON students FOR ALL
  USING (class_id IN (SELECT id FROM classes WHERE teacher_id = auth.uid()));
CREATE POLICY "Student can view own record"
  ON students FOR SELECT USING (auth_id = auth.uid());


-- ────────────────────────────────────────────────────────────
-- 4. LESSONS
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lessons (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  class_id   uuid REFERENCES classes(id) ON DELETE CASCADE,
  title      text NOT NULL,
  topic      text,
  created_by uuid REFERENCES profiles(id),
  status     text DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
  created_at timestamptz DEFAULT now()
);

ALTER TABLE lessons ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Teachers can manage own lessons"
  ON lessons FOR ALL
  USING (created_by = auth.uid()
      OR class_id IN (SELECT id FROM classes WHERE teacher_id = auth.uid()));
CREATE POLICY "Students can view active lessons in their class"
  ON lessons FOR SELECT
  USING (status = 'active'
     AND class_id IN (SELECT class_id FROM students WHERE auth_id = auth.uid()));


-- ────────────────────────────────────────────────────────────
-- 5. EXERCISES
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS exercises (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lesson_id   uuid REFERENCES lessons(id) ON DELETE CASCADE,
  he_text     text NOT NULL,
  en_text     text NOT NULL,
  difficulty  integer DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 5),
  approved    boolean DEFAULT false,
  created_by  uuid REFERENCES profiles(id),
  created_at  timestamptz DEFAULT now()
);

ALTER TABLE exercises ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Teachers can manage exercises in own lessons"
  ON exercises FOR ALL
  USING (lesson_id IN (
    SELECT l.id FROM lessons l
    JOIN classes c ON c.id = l.class_id
    WHERE c.teacher_id = auth.uid()
  ));
CREATE POLICY "Students can view approved exercises"
  ON exercises FOR SELECT
  USING (approved = true
     AND lesson_id IN (
       SELECT l.id FROM lessons l
       JOIN students s ON s.class_id = l.class_id
       WHERE s.auth_id = auth.uid() AND l.status = 'active'
     ));


-- ────────────────────────────────────────────────────────────
-- 6. PRACTICE SESSIONS
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  student_id       uuid REFERENCES students(id) ON DELETE CASCADE,
  lesson_id        uuid REFERENCES lessons(id) ON DELETE SET NULL,
  mode             text DEFAULT 'practice' CHECK (mode IN ('practice', 'placement', 'review')),
  started_at       timestamptz DEFAULT now(),
  ended_at         timestamptz,
  total_exercises  integer DEFAULT 0,
  mastery_count    integer DEFAULT 0
);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Students can manage own sessions"
  ON sessions FOR ALL
  USING (student_id IN (SELECT id FROM students WHERE auth_id = auth.uid()));
CREATE POLICY "Teachers can view sessions of own students"
  ON sessions FOR SELECT
  USING (student_id IN (
    SELECT s.id FROM students s
    JOIN classes c ON c.id = s.class_id
    WHERE c.teacher_id = auth.uid()
  ));


-- ────────────────────────────────────────────────────────────
-- 7. ATTEMPTS  ← THE IP
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attempts (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id         uuid REFERENCES sessions(id) ON DELETE CASCADE,
  student_id         uuid REFERENCES students(id) ON DELETE CASCADE,
  exercise_id        uuid REFERENCES exercises(id) ON DELETE SET NULL,
  spoken_text        text,
  score              numeric(5,2),
  passed             boolean,
  attempt_number     integer DEFAULT 1,
  mastery_required   integer,
  mastery_completed  integer DEFAULT 0,
  time_to_speak      numeric(6,2),  -- seconds from question display to first speech
  error_tags         jsonb,         -- {"missing_words": ["worked"], "pronunciation": ["engineer"]}
  created_at         timestamptz DEFAULT now()
);

ALTER TABLE attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Students can insert own attempts"
  ON attempts FOR INSERT
  WITH CHECK (student_id IN (SELECT id FROM students WHERE auth_id = auth.uid()));
CREATE POLICY "Teachers can view attempts of own students"
  ON attempts FOR SELECT
  USING (student_id IN (
    SELECT s.id FROM students s
    JOIN classes c ON c.id = s.class_id
    WHERE c.teacher_id = auth.uid()
  ));
CREATE POLICY "Students can view own attempts"
  ON attempts FOR SELECT
  USING (student_id IN (SELECT id FROM students WHERE auth_id = auth.uid()));


-- ────────────────────────────────────────────────────────────
-- HELPER VIEW: teacher dashboard summary
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW student_progress AS
SELECT
  s.id            AS student_id,
  s.display_name,
  s.class_id,
  s.level,
  COUNT(a.id)                                  AS total_attempts,
  COUNT(a.id) FILTER (WHERE a.passed = true)   AS passed_count,
  ROUND(AVG(a.score), 1)                       AS avg_score,
  ROUND(AVG(a.time_to_speak), 1)               AS avg_response_time,
  MAX(a.created_at)                            AS last_active
FROM students s
LEFT JOIN attempts a ON a.student_id = s.id
GROUP BY s.id, s.display_name, s.class_id, s.level;
