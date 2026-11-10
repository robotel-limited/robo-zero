"""Teste pentru secțiunea Hermes skills din weekly_report."""
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.weekly_report import _hermes_skills_section


def test_no_git_repo(tmp_path):
    """Dir fără .git -> secțiune goală, fără excepție."""
    result = _hermes_skills_section(7, skills_dir=tmp_path)
    assert result == []


def test_no_commits_in_window(tmp_path):
    """Repo git fără commit-uri recente -> 'Nicio modificare'."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"],
                   check=True, capture_output=True)
    result = _hermes_skills_section(7, skills_dir=tmp_path)
    assert any("Nicio modificare" in ln for ln in result)


def test_commits_found(tmp_path, monkeypatch):
    """subprocess.run mockat: 1 sha returnat -> secțiunea conține commit-uri."""
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        result = types.SimpleNamespace(stdout="", returncode=0)
        if "--format=%H" in cmd:
            result.stdout = "abc1234\n"
        elif "--stat" in cmd:
            result.stdout = "  skills/foo.md | 5 +++++\n"
        return result

    import scripts.weekly_report as wr
    monkeypatch.setattr(wr, "subprocess", types.SimpleNamespace(run=fake_run))
    result = _hermes_skills_section(7, skills_dir=tmp_path)
    assert any("1 commit" in ln for ln in result)
    assert any("foo.md" in ln for ln in result)
