#!/usr/bin/env python3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[0] / "bin" / "seat-usage"
NEEDLE = "| no data yet"
NEW = NEEDLE + " updated-from-origin"
DAY2 = NEEDLE + " updated-from-origin second-day"
MISS = (
    "[seat-usage] no cached line (FileNotFoundError); run ~/.local/bin/seat-usage\n"
    "  muse     | no data yet\n"
)
MUSE_NEW = "  muse     | no data yet updated-from-origin\n"
MUSE_DAY2 = "  muse     | no data yet updated-from-origin second-day\n"

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def git(*args, repo=None):
    cmd = ["git"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    cmd += [str(arg) for arg in args]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "tests",
        "GIT_AUTHOR_EMAIL": "tests@example.com",
        "GIT_COMMITTER_NAME": "tests",
        "GIT_COMMITTER_EMAIL": "tests@example.com",
        "GIT_TERMINAL_PROMPT": "0",
    }
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise AssertionError(f"{cmd} -> {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def write_script(repo, text):
    path = Path(repo) / "bin" / "seat-usage"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def source_text():
    text = SOURCE.read_text()
    assert text.count(NEEDLE) == 2, text.count(NEEDLE)
    return text


def new_checkout(tmp, *, push_upstream=False, local_commit=False):
    text = source_text()
    base = Path(tmp) / "base"
    origin = Path(tmp) / "origin.git"
    work = Path(tmp) / "work"
    base.mkdir()
    write_script(base, text)
    git("init", "-b", "main", repo=base)
    git("add", "-A", repo=base)
    git("commit", "-m", "base", repo=base)
    git("clone", "--bare", str(base), str(origin))
    git("clone", str(origin), str(work))
    if local_commit:
        (work / "LOCAL_NOTE").write_text("keep\n")
        git("add", "LOCAL_NOTE", repo=work)
        git("commit", "-m", "local", repo=work)
    if push_upstream:
        write_script(base, text.replace(NEEDLE, NEW))
        git("add", "-A", repo=base)
        git("commit", "-m", "upstream", repo=base)
        git("remote", "add", "origin", str(origin), repo=base)
        git("push", "origin", "main", repo=base)
    return work, work / "bin" / "seat-usage", base


def run_seat(script, home, args, extra=None, timeout=30):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "UTC",
    }
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def head(repo):
    return git("rev-parse", "HEAD", repo=repo)


def stamp_path(home):
    return Path(home) / ".cache" / "quartermaster" / "seat-usage-update"


def age_stamp(home):
    path = stamp_path(home)
    path.write_text(f"{time.time() - 86401:.3f}\n")


def stayed(proc, work, before):
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stderr == "", proc.stderr
    assert "[seat-usage] updated" not in proc.stdout, proc.stdout
    assert "updated-from-origin" not in proc.stdout, proc.stdout
    assert head(work) == before


@test
def fast_forward_prints_updated_and_reruns_the_pulled_script():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, base = new_checkout(tmp, push_upstream=True)
        git("config", "pull.rebase", "true", repo=work)
        git("config", "rebase.autoStash", "true", repo=work)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        muse = home / ".cache" / "quartermaster" / "muse-usage.json"
        muse.parent.mkdir(parents=True)
        muse.write_text('{"keep":true}\n')
        link = home / "bin" / "seat-usage"
        link.parent.mkdir()
        link.symlink_to(script)
        proc = run_seat(link, home, [])
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        assert proc.stderr == "", proc.stderr
        assert proc.stdout.startswith("[seat-usage] updated\n"), proc.stdout
        assert MUSE_NEW in proc.stdout, proc.stdout
        assert head(work) != before
        assert head(work) == git("rev-parse", "origin/main", repo=work)
        assert "updated-from-origin" in script.read_text()
        assert git("status", "--porcelain", repo=work) == ""
        assert git("stash", "list", repo=work) == ""
        assert muse.read_text() == '{"keep":true}\n'
        write_script(base, source_text().replace(NEEDLE, DAY2))
        git("add", "-A", repo=base)
        git("commit", "-m", "second day", repo=base)
        git("push", "origin", "main", repo=base)
        again = run_seat(link, home, [])
        assert again.returncode == 0 and again.stderr == "", (again.returncode, again.stderr, again.stdout)
        assert "[seat-usage] updated" not in again.stdout, again.stdout
        assert MUSE_NEW in again.stdout, again.stdout
        assert "second-day" not in script.read_text()
        age_stamp(home)
        later = run_seat(link, home, [])
        assert later.returncode == 0 and later.stderr == "", (later.returncode, later.stderr, later.stdout)
        assert later.stdout.startswith("[seat-usage] updated\n"), later.stdout
        assert MUSE_DAY2 in later.stdout, later.stdout
        assert "second-day" in script.read_text()
        assert muse.read_text() == '{"keep":true}\n'
        assert git("stash", "list", repo=work) == ""


@test
def dirty_tree_keeps_the_local_edit():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True)
        before = head(work)
        script.write_text(script.read_text() + "\n# local edit\n")
        home = Path(tmp) / "home"
        home.mkdir()
        proc = run_seat(script, home, [])
        stayed(proc, work, before)
        assert script.read_text().endswith("# local edit\n")
        assert "updated-from-origin" not in script.read_text()
        assert not stamp_path(home).exists()


@test
def other_branch_stays_put():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True)
        git("checkout", "-b", "side", repo=work)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        proc = run_seat(script, home, [])
        stayed(proc, work, before)
        assert git("symbolic-ref", "--short", "HEAD", repo=work) == "side"
        assert "updated-from-origin" not in script.read_text()
        assert not stamp_path(home).exists()


@test
def detached_head_stays_put():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True)
        git("checkout", "--detach", repo=work)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        proc = run_seat(script, home, [])
        stayed(proc, work, before)
        assert git("rev-parse", "--abbrev-ref", "HEAD", repo=work) == "HEAD"
        assert "updated-from-origin" not in script.read_text()
        assert not stamp_path(home).exists()


@test
def diverged_history_is_not_reset():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True, local_commit=True)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        proc = run_seat(script, home, [])
        stayed(proc, work, before)
        assert (work / "LOCAL_NOTE").read_text() == "keep\n"
        assert "updated-from-origin" not in script.read_text()
        assert git("stash", "list", repo=work) == ""


@test
def local_commits_ahead_are_kept():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, local_commit=True)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        proc = run_seat(script, home, [])
        stayed(proc, work, before)
        assert (work / "LOCAL_NOTE").read_text() == "keep\n"
        assert git("stash", "list", repo=work) == ""


@test
def opt_out_skips_until_the_variable_is_unset():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        held = run_seat(script, home, [], {"QUARTERMASTER_NO_UPDATE": "1"})
        stayed(held, work, before)
        assert not stamp_path(home).exists()
        pulled = run_seat(script, home, [], {"QUARTERMASTER_NO_UPDATE": "0"})
        assert pulled.returncode == 0 and pulled.stderr == "", (pulled.returncode, pulled.stderr, pulled.stdout)
        assert pulled.stdout.startswith("[seat-usage] updated\n"), pulled.stdout
        assert MUSE_NEW in pulled.stdout, pulled.stdout
        assert "updated-from-origin" in script.read_text()


@test
def route_quiet_and_voters_do_not_pull():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        route = run_seat(script, home, ["--route", "voter"])
        assert route.returncode == 2, (route.returncode, route.stderr, route.stdout)
        assert route.stdout == "", route.stdout
        assert route.stderr == "voters do not route\n", route.stderr
        council = run_seat(script, home, ["--cached"], {"COUNCIL_VOTER": "1"})
        assert council.returncode == 0, (council.returncode, council.stderr, council.stdout)
        assert council.stdout == "" and council.stderr == "", (council.stdout, council.stderr)
        conclave = run_seat(script, home, ["--cached"], {"CONCLAVE_VOTER": "1"})
        assert conclave.returncode == 0 and conclave.stderr == "", (conclave.returncode, conclave.stderr, conclave.stdout)
        assert conclave.stdout == MISS, conclave.stdout
        quiet = run_seat(script, home, ["--quiet"])
        assert quiet.returncode == 0, (quiet.returncode, quiet.stderr, quiet.stdout)
        assert quiet.stdout == "", quiet.stdout
        assert "[seat-usage] updated" not in quiet.stderr
        assert head(work) == before
        assert "updated-from-origin" not in script.read_text()
        assert not stamp_path(home).exists()


@test
def failed_fetch_waits_a_day_and_then_updates():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True)
        before = head(work)
        origin = git("remote", "get-url", "origin", repo=work)
        git("remote", "set-url", "origin", str(Path(tmp) / "missing.git"), repo=work)
        home = Path(tmp) / "home"
        home.mkdir()
        first = run_seat(script, home, [])
        stayed(first, work, before)
        assert stamp_path(home).is_file()
        git("remote", "set-url", "origin", origin, repo=work)
        second = run_seat(script, home, [])
        stayed(second, work, before)
        age_stamp(home)
        third = run_seat(script, home, [])
        assert third.returncode == 0 and third.stderr == "", (third.returncode, third.stderr, third.stdout)
        assert third.stdout.startswith("[seat-usage] updated\n"), third.stdout
        assert MUSE_NEW in third.stdout, third.stdout
        assert head(work) == git("rev-parse", "origin/main", repo=work)
        assert "updated-from-origin" in script.read_text()


@test
def a_copy_outside_a_checkout_runs_the_current_script():
    with tempfile.TemporaryDirectory() as tmp:
        loose = Path(tmp) / "loose" / "seat-usage"
        loose.parent.mkdir()
        loose.write_text(source_text())
        loose.chmod(0o755)
        home = Path(tmp) / "home"
        home.mkdir()
        proc = run_seat(loose, home, [])
        assert proc.returncode == 0 and proc.stderr == "", (proc.returncode, proc.stderr, proc.stdout)
        assert "[seat-usage] updated" not in proc.stdout, proc.stdout
        assert not stamp_path(home).exists()


@test
def cached_run_does_not_pull():
    with tempfile.TemporaryDirectory() as tmp:
        work, script, _base = new_checkout(tmp, push_upstream=True)
        before = head(work)
        home = Path(tmp) / "home"
        home.mkdir()
        proc = run_seat(script, home, ["--cached"])
        assert proc.returncode == 0 and proc.stderr == "", (proc.returncode, proc.stderr, proc.stdout)
        assert proc.stdout == MISS, proc.stdout
        assert head(work) == before
        assert "updated-from-origin" not in script.read_text()
        assert not stamp_path(home).exists()
        pulled = run_seat(script, home, [])
        assert pulled.returncode == 0 and pulled.stderr == "", (pulled.returncode, pulled.stderr, pulled.stdout)
        assert pulled.stdout.startswith("[seat-usage] updated\n"), pulled.stdout
        assert MUSE_NEW in pulled.stdout, pulled.stdout
        assert head(work) == git("rev-parse", "origin/main", repo=work)


def main():
    passed = failed = 0
    for fn in TESTS:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        else:
            passed += 1
            print(f"ok {fn.__name__}")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
