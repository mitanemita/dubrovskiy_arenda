"""Защита обновления от отката работающей версии (без Docker и сети)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


DEPLOY = Path(__file__).resolve().parents[1] / "deploy.sh"


def run(*args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=True).stdout.strip()


@pytest.fixture
def deploy_repo(tmp_path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    run("git", "init", "--bare", str(remote), cwd=tmp_path)
    run("git", "init", "-b", "main", str(repo), cwd=tmp_path)
    run("git", "config", "user.email", "test@example.com", cwd=repo)
    run("git", "config", "user.name", "Test", cwd=repo)
    shutil.copy(DEPLOY, repo / "deploy.sh")
    (repo / ".env").write_text("")
    run("git", "add", "deploy.sh", cwd=repo)
    run("git", "commit", "-m", "initial", cwd=repo)
    run("git", "remote", "add", "origin", str(remote), cwd=repo)
    run("git", "push", "-u", "origin", "main", cwd=repo)
    old = run("git", "rev-parse", "HEAD", cwd=repo)
    (repo / "newer").write_text("newer version")
    run("git", "add", "newer", cwd=repo)
    run("git", "commit", "-m", "newer", cwd=repo)
    newer = run("git", "rev-parse", "HEAD", cwd=repo)
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    docker = fakebin / "docker"
    docker.write_text('''#!/usr/bin/env bash
if [ "$1" = info ]; then exit 0; fi
if [ "$1" = compose ] && [ "$2" = ps ]; then
  [ "${FAKE_RUNNING:-}" = 1 ] && echo bot
  exit 0
fi
if [ "$1" = compose ] && [ "$2" = exec ]; then echo "$FAKE_COMMIT"; exit 0; fi
if [ "$1" = compose ] && [ "$2" = up ]; then touch "${FAKE_UP_MARKER}"; exit 0; fi
exit 1
''')
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update(PATH=f"{fakebin}:{env['PATH']}", FAKE_UP_MARKER=str(tmp_path / "started"))
    return repo, env, old, newer, tmp_path / "started"


def test_refuses_target_behind_checkout(deploy_repo):
    repo, env, old, newer, marker = deploy_repo
    result = subprocess.run(["bash", "deploy.sh"], cwd=repo, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "не содержит текущий коммит" in result.stderr
    assert not marker.exists()
    assert run("git", "rev-parse", "HEAD", cwd=repo) == newer


def test_refuses_target_behind_running_bot(deploy_repo):
    repo, env, old, newer, marker = deploy_repo
    run("git", "checkout", "--detach", old, cwd=repo)
    env.update(FAKE_RUNNING="1", FAKE_COMMIT=newer[:7])
    result = subprocess.run(["bash", "deploy.sh"], cwd=repo, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "не содержит коммит запущенного бота" in result.stderr
    assert not marker.exists()


def test_refuses_unknown_running_bot_version(deploy_repo):
    repo, env, old, newer, marker = deploy_repo
    run("git", "checkout", "--detach", old, cwd=repo)
    env.update(FAKE_RUNNING="1", FAKE_COMMIT="unknown")
    result = subprocess.run(["bash", "deploy.sh"], cwd=repo, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "Коммит запущенного бота неизвестен" in result.stderr
    assert not marker.exists()


def test_allows_target_with_current_version(deploy_repo):
    repo, env, old, newer, marker = deploy_repo
    run("git", "push", "origin", "main", cwd=repo)
    result = subprocess.run(["bash", "deploy.sh"], cwd=repo, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert marker.exists()
