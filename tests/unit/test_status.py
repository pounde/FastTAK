"""Tests for app.status — Status IntEnum."""


class TestStatus:
    def test_ordering(self):
        from app.status import Status

        assert Status.ok < Status.note < Status.warning < Status.critical

    def test_comparison(self):
        from app.status import Status

        assert Status.warning >= Status.warning
        assert Status.critical > Status.warning
        assert not Status.note > Status.warning

    def test_from_string(self):
        from app.status import Status

        assert Status["ok"] == Status.ok
        assert Status["critical"] == Status.critical
