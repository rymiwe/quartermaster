#!/usr/bin/env python3
import os
import pwd
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
INSTALL = REPO / "install.sh"
BIN = REPO / "bin" / "seat-usage"
HOOK = REPO / "hooks" / "seat-route-hook"
EXAMPLE = REPO / "renewals.example.json"

DEST_BIN = Path(".local/bin/seat-usage")
DEST_HOOK = Path(".claude/hooks/seat-route-hook")
RENEWALS = Path(".config/seat-usage/renewals.json")
SETTINGS = Path(".claude/settings.json")

FOREIGN_BIN = b"foreign seat-usage bytes\n"
FOREIGN_HOOK = b"foreign seat-route-hook bytes\n"
FOREIGN_MODE = 0o640
RENEWALS_BYTES = b'{"private":"do-not-touch"}\n'
SETTINGS_BYTES = b'{"hooks":"owned-by-dotfiles"}\n'

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
GUARDED = (
    REAL_HOME / ".local",
    REAL_HOME / ".claude",
    REAL_HOME / ".config" / "seat-usage",
)


def fingerprint(path):
    if path.is_symlink() or path.exists():
        st = path.lstat()
        return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns)
    return None


REAL_BEFORE = {str(path): fingerprint(path) for path in GUARDED}

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def run(args, home):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [str(INSTALL), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def write_file(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def snapshot(root):
    rows = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in list(dirnames):
            path = base / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                rows.append(("link", rel, os.readlink(path)))
                dirnames.remove(name)
            else:
                rows.append(("dir", rel, stat.S_IMODE(path.stat().st_mode)))
        for name in filenames:
            path = base / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                rows.append(("link", rel, os.readlink(path)))
            else:
                rows.append(("file", rel, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)))
    rows.sort()
    return tuple(rows)


def assert_ok(proc):
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stderr == "", proc.stderr


def assert_repo_symlink(path, src):
    assert path.is_symlink(), (path, os.listdir(path.parent) if path.parent.exists() else None)
    assert os.readlink(path) == str(src.resolve()), (os.readlink(path), str(src.resolve()))
    assert path.exists(), path
    assert os.access(path, os.X_OK), path


def assert_backup(path, data, mode):
    backup = Path(str(path) + ".pre-quartermaster")
    assert backup.is_file(), backup
    assert not backup.is_symlink(), backup
    assert backup.read_bytes() == data, backup.read_bytes()
    assert stat.S_IMODE(backup.stat().st_mode) == mode, stat.S_IMODE(backup.stat().st_mode)
    return backup


def seed_foreign(prefix):
    write_file(prefix / DEST_BIN, FOREIGN_BIN, FOREIGN_MODE)
    write_file(prefix / DEST_HOOK, FOREIGN_HOOK, FOREIGN_MODE)


@test
def fresh_install_symlinks_the_checkout_and_check_passes():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        proc = run(["--prefix", str(prefix)], decoy)
        assert_ok(proc)
        assert_repo_symlink(prefix / DEST_BIN, BIN)
        assert_repo_symlink(prefix / DEST_HOOK, HOOK)
        assert not (prefix / RENEWALS).exists()
        assert str(EXAMPLE) in proc.stdout, proc.stdout
        assert str(prefix / RENEWALS) in proc.stdout, proc.stdout
        assert list(decoy.iterdir()) == [], list(decoy.iterdir())
        assert not Path(str(prefix / DEST_BIN) + ".pre-quartermaster").exists()
        check = run(["--prefix", str(prefix), "--check"], decoy)
        assert_ok(check)
        assert list(decoy.iterdir()) == [], list(decoy.iterdir())


@test
def home_env_symlinks_into_that_home():
    with tempfile.TemporaryDirectory() as home:
        proc = run([], home)
        assert_ok(proc)
        assert_repo_symlink(Path(home) / DEST_BIN, BIN)
        assert_repo_symlink(Path(home) / DEST_HOOK, HOOK)
        assert not (Path(home) / RENEWALS).exists()


@test
def install_over_an_existing_file_writes_a_backup():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        seed_foreign(prefix)
        proc = run(["--prefix", str(prefix)], decoy)
        assert_ok(proc)
        assert_repo_symlink(prefix / DEST_BIN, BIN)
        assert_repo_symlink(prefix / DEST_HOOK, HOOK)
        assert_backup(prefix / DEST_BIN, FOREIGN_BIN, FOREIGN_MODE)
        assert_backup(prefix / DEST_HOOK, FOREIGN_HOOK, FOREIGN_MODE)
        assert list(decoy.iterdir()) == []


@test
def rerun_does_not_replace_the_backup():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        seed_foreign(prefix)
        assert_ok(run(["--prefix", str(prefix)], decoy))
        bin_backup = assert_backup(prefix / DEST_BIN, FOREIGN_BIN, FOREIGN_MODE)
        hook_backup = assert_backup(prefix / DEST_HOOK, FOREIGN_HOOK, FOREIGN_MODE)
        bin_id = (bin_backup.stat().st_dev, bin_backup.stat().st_ino)
        hook_id = (hook_backup.stat().st_dev, hook_backup.stat().st_ino)
        link_bin = os.readlink(prefix / DEST_BIN)
        link_hook = os.readlink(prefix / DEST_HOOK)
        proc = run(["--prefix", str(prefix)], decoy)
        assert_ok(proc)
        assert "backup " not in proc.stdout, proc.stdout
        assert bin_backup.read_bytes() == FOREIGN_BIN, bin_backup.read_bytes()
        assert hook_backup.read_bytes() == FOREIGN_HOOK, hook_backup.read_bytes()
        assert (bin_backup.stat().st_dev, bin_backup.stat().st_ino) == bin_id
        assert (hook_backup.stat().st_dev, hook_backup.stat().st_ino) == hook_id
        assert os.readlink(prefix / DEST_BIN) == link_bin
        assert os.readlink(prefix / DEST_HOOK) == link_hook
        assert_repo_symlink(prefix / DEST_BIN, BIN)
        assert_repo_symlink(prefix / DEST_HOOK, HOOK)


@test
def dry_run_changes_nothing():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        seed_foreign(prefix)
        write_file(prefix / RENEWALS, RENEWALS_BYTES, 0o600)
        write_file(prefix / SETTINGS, SETTINGS_BYTES, 0o600)
        before = snapshot(prefix)
        proc = run(["--prefix", str(prefix), "--dry-run"], decoy)
        assert_ok(proc)
        assert snapshot(prefix) == before, snapshot(prefix)
        assert list(decoy.iterdir()) == []
        text = proc.stdout
        assert f"backup {prefix / DEST_BIN} -> {prefix / DEST_BIN}.pre-quartermaster" in text, text
        assert f"backup {prefix / DEST_HOOK} -> {prefix / DEST_HOOK}.pre-quartermaster" in text, text
        assert f"symlink {prefix / DEST_BIN} -> {BIN.resolve()}" in text, text
        assert f"symlink {prefix / DEST_HOOK} -> {HOOK.resolve()}" in text, text
        assert "is missing" not in text, text
        empty = Path(outer) / "empty"
        empty.mkdir()
        empty_before = snapshot(empty)
        empty_proc = run(["--prefix", str(empty), "--dry-run"], decoy)
        assert_ok(empty_proc)
        assert snapshot(empty) == empty_before
        assert not (empty / DEST_BIN).exists()
        assert not (empty / RENEWALS).exists()


@test
def uninstall_restores_the_original_bytes():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        seed_foreign(prefix)
        before = snapshot(prefix)
        assert_ok(run(["--prefix", str(prefix)], decoy))
        assert (prefix / DEST_BIN).is_symlink()
        proc = run(["--prefix", str(prefix), "--uninstall"], decoy)
        assert_ok(proc)
        assert snapshot(prefix) == before, snapshot(prefix)
        assert (prefix / DEST_BIN).read_bytes() == FOREIGN_BIN
        assert (prefix / DEST_HOOK).read_bytes() == FOREIGN_HOOK
        assert stat.S_IMODE((prefix / DEST_BIN).stat().st_mode) == FOREIGN_MODE
        assert stat.S_IMODE((prefix / DEST_HOOK).stat().st_mode) == FOREIGN_MODE
        assert not (prefix / DEST_BIN).is_symlink()
        check = run(["--prefix", str(prefix), "--check"], decoy)
        assert check.returncode != 0, (check.returncode, check.stdout, check.stderr)
        assert snapshot(prefix) == before


@test
def check_fails_on_a_broken_link():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        assert_ok(run(["--prefix", str(prefix)], decoy))
        dest = prefix / DEST_BIN
        dest.unlink()
        dest.symlink_to("/no/such/seat-usage-target")
        assert not dest.exists()
        before = snapshot(prefix)
        proc = run(["--prefix", str(prefix), "--check"], decoy)
        assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
        assert snapshot(prefix) == before
        assert os.readlink(dest) == "/no/such/seat-usage-target"
        assert_repo_symlink(prefix / DEST_HOOK, HOOK)
        assert not (prefix / RENEWALS).exists()


@test
def copy_install_uninstall_restores_the_original_bytes():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        seed_foreign(prefix)
        before = snapshot(prefix)
        proc = run(["--prefix", str(prefix), "--copy"], decoy)
        assert_ok(proc)
        for dest, src in ((prefix / DEST_BIN, BIN), (prefix / DEST_HOOK, HOOK)):
            assert dest.is_file() and not dest.is_symlink(), dest
            assert dest.read_bytes() == src.read_bytes(), dest
            assert os.access(dest, os.X_OK), dest
        bin_backup = assert_backup(prefix / DEST_BIN, FOREIGN_BIN, FOREIGN_MODE)
        hook_backup = assert_backup(prefix / DEST_HOOK, FOREIGN_HOOK, FOREIGN_MODE)
        bin_id = (bin_backup.stat().st_dev, bin_backup.stat().st_ino)
        hook_id = (hook_backup.stat().st_dev, hook_backup.stat().st_ino)
        again = run(["--prefix", str(prefix), "--copy"], decoy)
        assert_ok(again)
        assert "backup " not in again.stdout, again.stdout
        assert (bin_backup.stat().st_dev, bin_backup.stat().st_ino) == bin_id
        assert (hook_backup.stat().st_dev, hook_backup.stat().st_ino) == hook_id
        assert bin_backup.read_bytes() == FOREIGN_BIN
        check = run(["--prefix", str(prefix), "--check"], decoy)
        assert_ok(check)
        proc = run(["--prefix", str(prefix), "--uninstall"], decoy)
        assert_ok(proc)
        assert snapshot(prefix) == before, snapshot(prefix)
        assert (prefix / DEST_BIN).read_bytes() == FOREIGN_BIN
        assert (prefix / DEST_HOOK).read_bytes() == FOREIGN_HOOK


@test
def renewals_json_is_never_touched():
    with tempfile.TemporaryDirectory() as outer:
        prefix = Path(outer) / "prefix"
        decoy = Path(outer) / "decoy-home"
        prefix.mkdir()
        decoy.mkdir()
        write_file(prefix / RENEWALS, RENEWALS_BYTES, 0o600)
        write_file(prefix / SETTINGS, SETTINGS_BYTES, 0o600)
        renewals = prefix / RENEWALS
        settings = prefix / SETTINGS
        renewals_id = (renewals.stat().st_dev, renewals.stat().st_ino)
        settings_id = (settings.stat().st_dev, settings.stat().st_ino)

        def unchanged():
            assert renewals.read_bytes() == RENEWALS_BYTES
            assert settings.read_bytes() == SETTINGS_BYTES
            assert stat.S_IMODE(renewals.stat().st_mode) == 0o600
            assert stat.S_IMODE(settings.stat().st_mode) == 0o600
            assert (renewals.stat().st_dev, renewals.stat().st_ino) == renewals_id
            assert (settings.stat().st_dev, settings.stat().st_ino) == settings_id

        for args in (
            ["--prefix", str(prefix)],
            ["--prefix", str(prefix)],
            ["--prefix", str(prefix), "--dry-run"],
            ["--prefix", str(prefix), "--copy"],
            ["--prefix", str(prefix), "--check"],
            ["--prefix", str(prefix), "--uninstall"],
        ):
            proc = run(args, decoy)
            assert proc.returncode == 0, (args, proc.returncode, proc.stdout, proc.stderr)
            assert "is missing" not in proc.stdout, (args, proc.stdout)
            unchanged()

        absent = Path(outer) / "absent"
        absent.mkdir()
        proc = run(["--prefix", str(absent)], decoy)
        assert_ok(proc)
        assert not (absent / RENEWALS).exists(), "created renewals.json"
        assert not (absent / RENEWALS).is_symlink(), "created renewals.json"
        assert str(EXAMPLE) in proc.stdout, proc.stdout
        assert str(absent / RENEWALS) in proc.stdout, proc.stdout
        unchanged()


@test
def real_home_seat_paths_are_unchanged():
    after = {str(path): fingerprint(path) for path in GUARDED}
    assert after == REAL_BEFORE, (REAL_BEFORE, after)


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
