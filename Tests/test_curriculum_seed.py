import app as app_module


class FakeSeedCursor:
    def __init__(self, subjects, classes, admin_ids, class_teachers):
        self.subjects = subjects
        self.classes = classes
        self.admin_ids = admin_ids
        self.class_teachers = class_teachers
        self.row = None
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=None):
        if "SELECT id" in query and "FROM users" in query:
            assert parameters is None
            self.rows = [{"id": user_id} for user_id in self.admin_ids]
            return

        assert "ON CONFLICT (name) DO NOTHING" in query
        if "INSERT INTO subjects (name)" in query:
            (name,) = parameters
            records = self.subjects
        elif "INSERT INTO classes (name, class_teacher_id)" in query:
            name, admin_id = parameters
            records = self.classes
        else:
            raise AssertionError(f"Unexpected SQL in seed command: {query}")

        if name in records:
            self.row = None
            return
        records.add(name)
        if "INSERT INTO classes" in query:
            self.class_teachers[name] = admin_id
        self.row = {"id": len(records)}

    def fetchone(self):
        row = self.row
        self.row = None
        return row

    def fetchall(self):
        rows = self.rows
        self.rows = []
        return rows


class FakeSeedConnection:
    def __init__(self, subjects, classes, admin_ids):
        self.subjects = subjects
        self.classes = classes
        self.admin_ids = admin_ids
        self.class_teachers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return FakeSeedCursor(
            self.subjects,
            self.classes,
            self.admin_ids,
            self.class_teachers,
        )


def test_seed_curriculum_command_is_idempotent_and_audited(
    app,
    monkeypatch,
):
    subjects = set()
    classes = set()
    audits = []
    connection = FakeSeedConnection(subjects, classes, [7])
    monkeypatch.setattr(
        app_module,
        "connect_database",
        lambda: connection,
    )
    monkeypatch.setattr(
        app_module,
        "record_audit_event",
        lambda user_id, action, ip_address, *, connection: audits.append(
            (user_id, action, ip_address)
        ),
    )
    runner = app.test_cli_runner()

    first = runner.invoke(args=["seed-curriculum"], input="y\n")
    second = runner.invoke(args=["seed-curriculum"], input="y\n")

    assert first.exit_code == 0
    assert "Subjects: added 25; 0 already existed." in first.output
    assert "Classes: added 15; 0 already existed." in first.output
    assert second.exit_code == 0
    assert "Subjects: added 0; 25 already existed." in second.output
    assert "Classes: added 0; 15 already existed." in second.output
    assert len(subjects) == 25
    assert len(classes) == 15
    assert subjects == set(app_module.CURRICULUM_SUBJECTS)
    assert classes == set(app_module.CURRICULUM_CLASSES)
    assert set(connection.class_teachers.values()) == {7}
    assert len(audits) == 2
    assert audits == [
        (None, "curriculum_seeded", None),
        (None, "curriculum_seeded", None),
    ]
    assert "No teacher-subject assignments were created." in first.output


def test_seed_curriculum_decline_does_not_connect_to_database(app, monkeypatch):
    def fail_if_connected():
        raise AssertionError("Database connection should not be opened.")

    monkeypatch.setattr(app_module, "connect_database", fail_if_connected)
    result = app.test_cli_runner().invoke(
        args=["seed-curriculum"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Curriculum seeding cancelled." in result.output


def test_seed_curriculum_requires_active_admin_before_insert(app, monkeypatch):
    subjects = set()
    classes = set()
    audits = []
    connection = FakeSeedConnection(subjects, classes, [])
    monkeypatch.setattr(app_module, "connect_database", lambda: connection)
    monkeypatch.setattr(
        app_module,
        "record_audit_event",
        lambda *args, **kwargs: audits.append((args, kwargs)),
    )

    result = app.test_cli_runner().invoke(
        args=["seed-curriculum"],
        input="y\n",
    )

    assert result.exit_code != 0
    assert "No active administrator account is available" in result.output
    assert subjects == set()
    assert classes == set()
    assert audits == []
