-- STEP 2: Enable RLS and add policies
-- Run AFTER schema_step1_tables.sql completes successfully

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE classes  ENABLE ROW LEVEL SECURITY;
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE lessons  ENABLE ROW LEVEL SECURITY;
ALTER TABLE exercises ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE attempts ENABLE ROW LEVEL SECURITY;

-- profiles
CREATE POLICY "Users can read own profile"   ON profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY "Users can update own profile" ON profiles FOR UPDATE USING (auth.uid() = id);

-- classes
CREATE POLICY "Teachers can manage own classes" ON classes FOR ALL USING (teacher_id = auth.uid());
CREATE POLICY "Students can view their class"   ON classes FOR SELECT
  USING (id IN (SELECT class_id FROM students WHERE auth_id = auth.uid()));

-- students
CREATE POLICY "Teachers can manage students in own classes" ON students FOR ALL
  USING (class_id IN (SELECT id FROM classes WHERE teacher_id = auth.uid()));
CREATE POLICY "Student can view own record" ON students FOR SELECT USING (auth_id = auth.uid());

-- lessons
CREATE POLICY "Teachers can manage own lessons" ON lessons FOR ALL
  USING (created_by = auth.uid()
      OR class_id IN (SELECT id FROM classes WHERE teacher_id = auth.uid()));
CREATE POLICY "Students can view active lessons" ON lessons FOR SELECT
  USING (status = 'active'
     AND class_id IN (SELECT class_id FROM students WHERE auth_id = auth.uid()));

-- exercises
CREATE POLICY "Teachers can manage exercises" ON exercises FOR ALL
  USING (lesson_id IN (
    SELECT l.id FROM lessons l JOIN classes c ON c.id = l.class_id
    WHERE c.teacher_id = auth.uid()
  ));
CREATE POLICY "Students can view approved exercises" ON exercises FOR SELECT
  USING (approved = true AND lesson_id IN (
    SELECT l.id FROM lessons l JOIN students s ON s.class_id = l.class_id
    WHERE s.auth_id = auth.uid() AND l.status = 'active'
  ));

-- sessions
CREATE POLICY "Students can manage own sessions" ON sessions FOR ALL
  USING (student_id IN (SELECT id FROM students WHERE auth_id = auth.uid()));
CREATE POLICY "Teachers can view sessions of own students" ON sessions FOR SELECT
  USING (student_id IN (
    SELECT s.id FROM students s JOIN classes c ON c.id = s.class_id
    WHERE c.teacher_id = auth.uid()
  ));

-- attempts
CREATE POLICY "Students can insert own attempts" ON attempts FOR INSERT
  WITH CHECK (student_id IN (SELECT id FROM students WHERE auth_id = auth.uid()));
CREATE POLICY "Teachers can view attempts of own students" ON attempts FOR SELECT
  USING (student_id IN (
    SELECT s.id FROM students s JOIN classes c ON c.id = s.class_id
    WHERE c.teacher_id = auth.uid()
  ));
CREATE POLICY "Students can view own attempts" ON attempts FOR SELECT
  USING (student_id IN (SELECT id FROM students WHERE auth_id = auth.uid()));
