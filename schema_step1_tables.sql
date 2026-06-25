-- STEP 1: Create all tables (no policies yet)
-- Run this first, then run schema_step2_policies.sql

CREATE TABLE IF NOT EXISTS profiles (
  id         uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('teacher', 'student', 'admin')),
  name       text NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS classes (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name       text NOT NULL,
  grade      text,
  subject    text DEFAULT 'English',
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS students (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  class_id        uuid REFERENCES classes(id) ON DELETE SET NULL,
  auth_id         uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  display_name    text NOT NULL,
  level           integer DEFAULT 1 CHECK (level BETWEEN 1 AND 5),
  placement_score numeric(5,2),
  created_at      timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lessons (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  class_id   uuid REFERENCES classes(id) ON DELETE CASCADE,
  title      text NOT NULL,
  topic      text,
  created_by uuid REFERENCES profiles(id),
  status     text DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
  created_at timestamptz DEFAULT now()
);

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
  time_to_speak      numeric(6,2),
  error_tags         jsonb,
  created_at         timestamptz DEFAULT now()
);

CREATE OR REPLACE VIEW student_progress AS
SELECT
  s.id            AS student_id,
  s.display_name,
  s.class_id,
  s.level,
  COUNT(a.id)                                AS total_attempts,
  COUNT(a.id) FILTER (WHERE a.passed = true) AS passed_count,
  ROUND(AVG(a.score), 1)                     AS avg_score,
  ROUND(AVG(a.time_to_speak), 1)             AS avg_response_time,
  MAX(a.created_at)                          AS last_active
FROM students s
LEFT JOIN attempts a ON a.student_id = s.id
GROUP BY s.id, s.display_name, s.class_id, s.level;
