"""Configuration paths must not depend on the shell working directory."""

from pathlib import Path

from debate_agent_framework.config import DebateWebSettings


def test_relative_data_dir_is_stable_across_working_directories(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    settings = DebateWebSettings(data_dir="backend/data")
    expected_data_dir = settings.resolved_data_dir()
    expected_database_url = settings.resolved_database_url()

    monkeypatch.chdir(tmp_path)

    assert settings.resolved_data_dir() == expected_data_dir
    assert settings.resolved_database_url() == expected_database_url
    assert expected_data_dir.name == "data"
    assert expected_data_dir.parent.name == "backend"


def test_absolute_data_dir_is_preserved(tmp_path: Path) -> None:
    data_dir = tmp_path / "custom-data"
    settings = DebateWebSettings(data_dir=str(data_dir))

    assert settings.resolved_data_dir() == data_dir.resolve()
    assert settings.resolved_database_url().endswith("/custom-data/debate.db")
