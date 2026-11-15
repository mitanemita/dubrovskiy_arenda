"""Версия сборки видна в боте: метка UI + ветка@коммит + время сборки."""
from app import version


def test_version_line_from_build_env(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    monkeypatch.setenv("GIT_BRANCH", "main")
    monkeypatch.setenv("BUILD_DATE", "15.11.2026 10:00")
    line = version.version_line()
    assert line == f"{version.BOT_UI_VERSION} · main@abc1234 · собрано 15.11.2026 10:00"


def test_version_line_without_build_env(monkeypatch):
    for k in ("GIT_COMMIT", "GIT_BRANCH", "BUILD_DATE"):
        monkeypatch.delenv(k, raising=False)
    assert "unknown@unknown" in version.version_line()


def test_handlers_reexport_ui_version():
    from app.bot import handlers
    assert handlers.BOT_UI_VERSION == version.BOT_UI_VERSION
