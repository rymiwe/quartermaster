#!/usr/bin/env bash
set -eu

die() {
  printf 'install.sh: %s\n' "$*" >&2
  exit 1
}

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

usage() {
  cat <<EOF
usage: install.sh [--prefix DIR] [--copy] [--dry-run] [--uninstall] [--check]

Link ~/.local/bin/seat-usage and ~/.claude/hooks/seat-route-hook to this
checkout so a later git pull updates them. Pass --copy to copy the files
instead. --prefix replaces HOME as the install root. --dry-run prints each
action and writes nothing. --uninstall removes links or copies from this
checkout and restores <path>.pre-quartermaster. --check exits 0 when both
paths resolve to this checkout and are executable.

The script does not create, change, or delete
~/.config/seat-usage/renewals.json or ~/.claude/settings.json. When
renewals.json is missing, it prints a hint that names renewals.example.json.

On Windows, run this from Git Bash. Command Prompt and PowerShell cannot
run it. If Git Bash cannot create symlinks, pass --copy.
EOF
}

canon_existing_file() {
  path=$1
  guard=0
  while [ -L "$path" ]; do
    target=$(readlink "$path") || return 1
    case $target in
      /*) path=$target ;;
      *) path=$(dirname -- "$path")/$target ;;
    esac
    guard=$((guard + 1))
    if [ "$guard" -gt 32 ]; then
      return 1
    fi
  done
  if [ ! -e "$path" ]; then
    return 1
  fi
  dir=$(dirname -- "$path")
  base=$(basename -- "$path")
  dir=$(CDPATH= cd -- "$dir" && pwd -P) || return 1
  printf '%s/%s\n' "$dir" "$base"
}

exists_or_link() {
  [ -e "$1" ] || [ -L "$1" ]
}

refuse_protected() {
  case $1 in
    "$PREFIX/.config/seat-usage/renewals.json"|"$PREFIX/.claude/settings.json")
      die "refusing to change $1"
      ;;
  esac
}

say() {
  printf '%s\n' "$*"
}

PREFIX=${HOME-}
COPY=0
DRY=0
UNINSTALL=0
CHECK=0

while [ $# -gt 0 ]; do
  case $1 in
    --prefix)
      if [ $# -lt 2 ]; then
        die "--prefix needs a directory"
      fi
      PREFIX=$2
      shift 2
      ;;
    --copy)
      COPY=1
      shift
      ;;
    --dry-run)
      DRY=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    --check)
      CHECK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

if [ "$UNINSTALL" -eq 1 ] && [ "$CHECK" -eq 1 ]; then
  die "pass only one of --uninstall or --check"
fi
if [ -z "${PREFIX}" ]; then
  die "set HOME or pass --prefix"
fi
case $PREFIX in
  /*) ;;
  *) PREFIX=$(pwd)/$PREFIX ;;
esac
PREFIX=${PREFIX%/}
if [ -z "$PREFIX" ] || [ "$PREFIX" = "/" ]; then
  die "refusing prefix /"
fi

if [ ! -f "$ROOT/bin/seat-usage" ] || [ ! -f "$ROOT/hooks/seat-route-hook" ]; then
  die "missing bin/seat-usage or hooks/seat-route-hook under $ROOT"
fi
SRC_BIN=$(canon_existing_file "$ROOT/bin/seat-usage") || die "cannot resolve bin/seat-usage"
SRC_HOOK=$(canon_existing_file "$ROOT/hooks/seat-route-hook") || die "cannot resolve hooks/seat-route-hook"

is_our_link() {
  dest=$1
  src=$2
  if [ ! -L "$dest" ]; then
    return 1
  fi
  raw=$(readlink "$dest") || return 1
  if [ "$raw" = "$src" ]; then
    return 0
  fi
  resolved=$(canon_existing_file "$dest" 2>/dev/null) || return 1
  [ "$resolved" = "$src" ]
}

is_our_copy() {
  dest=$1
  src=$2
  if [ -L "$dest" ] || [ ! -f "$dest" ] || [ ! -f "$dest.quartermaster" ]; then
    return 1
  fi
  mark=$(cat "$dest.quartermaster") || return 1
  [ "$mark" = "$src" ]
}

mutate() {
  message=$1
  shift
  for arg in "$@"; do
    refuse_protected "$arg"
  done
  say "$message"
  if [ "$DRY" -eq 0 ]; then
    "$@"
  fi
}

backup_foreign() {
  dest=$1
  backup=$dest.pre-quartermaster
  if exists_or_link "$backup"; then
    die "refusing to overwrite $backup"
  fi
  mutate "backup $dest -> $backup" mv "$dest" "$backup"
}

install_one() {
  dest=$1
  src=$2
  if [ "$COPY" -eq 0 ] && is_our_link "$dest" "$src" && [ -e "$dest" ]; then
    say "unchanged $dest"
    return 0
  fi
  if [ "$COPY" -eq 1 ] && is_our_copy "$dest" "$src"; then
    if cmp -s "$dest" "$src" && [ -x "$dest" ]; then
      say "unchanged $dest"
      return 0
    fi
    if ! cmp -s "$dest" "$src"; then
      mutate "copy $src -> $dest" cp "$src" "$dest"
    fi
    if [ ! -x "$dest" ]; then
      mutate "chmod +x $dest" chmod +x "$dest"
    fi
    return 0
  fi
  if is_our_link "$dest" "$src" || is_our_copy "$dest" "$src"; then
    mutate "remove $dest" rm -f "$dest"
    if [ -f "$dest.quartermaster" ]; then
      mutate "remove $dest.quartermaster" rm -f "$dest.quartermaster"
    fi
  elif exists_or_link "$dest"; then
    if [ -d "$dest" ] && [ ! -L "$dest" ]; then
      die "$dest is a directory"
    fi
    backup_foreign "$dest"
  fi
  parent=$(dirname -- "$dest")
  if [ ! -d "$parent" ]; then
    mutate "mkdir $parent" mkdir -p "$parent"
  fi
  if [ "$COPY" -eq 1 ]; then
    mutate "copy $src -> $dest" cp "$src" "$dest"
    mutate "chmod +x $dest" chmod +x "$dest"
    mutate "mark $dest.quartermaster" sh -c 'printf "%s\n" "$1" > "$2"' sh "$src" "$dest.quartermaster"
  else
    mutate "symlink $dest -> $src" ln -s "$src" "$dest"
  fi
}

restore_backup() {
  dest=$1
  backup=$dest.pre-quartermaster
  if ! exists_or_link "$backup"; then
    return 0
  fi
  if exists_or_link "$dest"; then
    die "cannot restore $backup because $dest exists"
  fi
  mutate "restore $backup -> $dest" mv "$backup" "$dest"
}

uninstall_one() {
  dest=$1
  src=$2
  if is_our_link "$dest" "$src" || is_our_copy "$dest" "$src"; then
    mutate "remove $dest" rm -f "$dest"
    if [ -e "$dest.quartermaster" ] || [ -L "$dest.quartermaster" ]; then
      mutate "remove $dest.quartermaster" rm -f "$dest.quartermaster"
    fi
  fi
  restore_backup "$dest"
}

check_one() {
  dest=$1
  src=$2
  if [ -L "$dest" ] && [ ! -e "$dest" ]; then
    say "not-ok $dest broken link"
    return 1
  fi
  if [ ! -e "$dest" ]; then
    say "not-ok $dest missing"
    return 1
  fi
  if [ ! -x "$dest" ]; then
    say "not-ok $dest not executable"
    return 1
  fi
  if [ -L "$dest" ]; then
    resolved=$(canon_existing_file "$dest" 2>/dev/null) || resolved=
    if [ "$resolved" != "$src" ]; then
      say "not-ok $dest does not resolve to $src"
      return 1
    fi
    say "ok $dest"
    return 0
  fi
  if cmp -s "$dest" "$src"; then
    say "ok $dest"
    return 0
  fi
  say "not-ok $dest does not resolve to $src"
  return 1
}

hint_renewals() {
  renewals=$PREFIX/.config/seat-usage/renewals.json
  if [ -e "$renewals" ] || [ -L "$renewals" ]; then
    return 0
  fi
  say "hint: $renewals is missing; see $ROOT/renewals.example.json"
}

if [ "$CHECK" -eq 1 ]; then
  failed=0
  check_one "$PREFIX/.local/bin/seat-usage" "$SRC_BIN" || failed=1
  check_one "$PREFIX/.claude/hooks/seat-route-hook" "$SRC_HOOK" || failed=1
  exit "$failed"
fi

if [ "$UNINSTALL" -eq 1 ]; then
  uninstall_one "$PREFIX/.local/bin/seat-usage" "$SRC_BIN"
  uninstall_one "$PREFIX/.claude/hooks/seat-route-hook" "$SRC_HOOK"
  exit 0
fi

install_one "$PREFIX/.local/bin/seat-usage" "$SRC_BIN"
install_one "$PREFIX/.claude/hooks/seat-route-hook" "$SRC_HOOK"
hint_renewals
