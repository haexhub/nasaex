#!/usr/bin/env bash
# The archive lock of S2 (research D11, task T061). NOT product code.
#
#   archive-lock.sh lock REASON     refuse maintenance from now on, then remount read-only every
#                                   filesystem the SnapRAID config names: each archive data disk,
#                                   each parity disk and each content-file location (a content
#                                   directory on no locked filesystem gets a read-only bind mount
#                                   of itself, so the system disk stays writable)
#   archive-lock.sh unlock          remount them read-write, stop snapperd; the refusal is lifted
#                                   last
#   archive-lock.sh status          "locked" or "unlocked", the reason, one line per location
#   archive-lock.sh snapraid ARGS...        thin wrappers: while locked, refuse every command
#   archive-lock.sh snapraid-btrfs ARGS...  except fix and the reading ones; else run the tool
#
# The kernel enforces the lock: a write to a read-only filesystem fails with EROFS by every route
# (scheduled, manual, automatic, the real binaries). Creating, deleting and cleaning snapper
# snapshots write to the data disks and fail with them. The wrappers and archive_lock_guard only
# give the defined refusal: exit status 30 (the errno value of EROFS) and the line
#   archive-lock: refused: <action>: the archive is locked (<reason>)
# on stderr. mover-effects.sh sources this file and guards every move primitive.
#
# SnapRAID opens <first content file>.lock with O_CREAT before every command, fix and status
# included; lock creates that file before its location turns read-only.
#
# Limits, by design of S2: a filesystem with a file open for writing cannot be remounted
# read-only (EBUSY): lock then exits 1 naming it, and the refusal stays. A data disk that is not
# mounted is skipped; a replacement disk mounted after the lock is writable, because fix writes
# to it. The state lives in /run: a reboot mounts read-write from the fstab and forgets the lock;
# S4 makes it survive a reboot (research D11).

_AL_EROFS=30
# The commands of each tool that run while locked: fix (it writes only to the disk it fixes, the
# replacement) and the ones that only read. Every other command is refused, unknown ones too.
_AL_SNAPRAID_RUN="check diff dup fix list locate status devices smart probe up down"
_AL_SRB_RUN="check config dup fix list locate ls status"

_al_dir() { echo "${ARCHIVE_LOCK_DIR:-/run/archive-lock}"; }

# archive_lock_guard ACTION: return 0 when unlocked, else print the refusal and return 30.
archive_lock_guard() {
    local dir
    dir=$(_al_dir)
    [[ -e $dir/reason ]] || return 0
    echo "archive-lock: refused: $1: the archive is locked ($(<"$dir/reason"))" >&2
    return "$_AL_EROFS"
}

# _al_paths REGEX: the last field of every SnapRAID config line whose keyword matches.
_al_paths() {
    awk -v re="$1" '$1 ~ re { print $NF }' "${SNAPRAID_CONF:-/etc/snapraid.conf}"
}

_al_mount_of() { findmnt -n -o TARGET --target "$1"; }

_al_is_mount() { findmnt -n -o OPTIONS --mountpoint "$1" >/dev/null; }

_al_readonly() {
    local options
    options=$(findmnt -n -o OPTIONS --mountpoint "$1") || return 1
    [[ ,$options, == *,ro,* ]]
}

_al_record() { # <kind> <path>
    local state
    state="$(_al_dir)/mounts"
    grep -qxF "$1 $2" "$state" 2>/dev/null || echo "$1 $2" >>"$state"
}

archive_lock_lock() {
    local reason=${1:?usage: archive-lock.sh lock REASON}
    local dir path target first kind options failed=()
    local -A locked=()
    dir=$(_al_dir)

    mkdir -p "$dir"
    echo "$reason" >"$dir/reason" # the refusal holds from here on, also if a remount fails
    first=$(_al_paths '^content$' | head -n 1)
    if [[ -n $first && ! -e $first.lock ]]; then
        touch "$first.lock" # an existing one may already sit on a read-only location
    fi
    sync

    while read -r path; do
        target=$(_al_mount_of "$path")
        if [[ $target == / ]]; then
            echo "$path: not mounted, skipped"
            continue
        fi
        [[ -z ${locked[$target]-} ]] || continue
        locked[$target]=1
        _al_record remount "$target"
        _al_readonly "$target" || mount -o remount,ro "$target" || failed+=("$target")
    done < <(_al_paths '^(data|([2-6z]-)?parity)$')

    while read -r path; do
        path=$(dirname "$path")
        target=$(_al_mount_of "$path")
        [[ -z ${locked[$target]-} ]] || continue # on a filesystem locked above
        if [[ $target == "$path" ]] && ! grep -qxF "bind $path" "$dir/mounts" 2>/dev/null; then
            kind=remount options=remount,ro # a filesystem of its own
        else
            kind=bind options=remount,bind,ro
            if [[ $target != "$path" ]]; then # not bound by an earlier lock
                mount --bind "$path" "$path" || {
                    failed+=("$path")
                    continue
                }
            fi
        fi
        locked[$path]=1
        _al_record "$kind" "$path"
        _al_readonly "$path" || mount -o "$options" "$path" || failed+=("$path")
    done < <(_al_paths '^content$')

    if ((${#failed[@]} > 0)); then
        echo "archive-lock: not read-only, the refusal stays: ${failed[*]}" >&2
        return 1
    fi
}

archive_lock_unlock() {
    local dir kind path failed=()
    dir=$(_al_dir)
    [[ -e $dir/reason ]] || return 0
    while read -r kind path; do
        _al_is_mount "$path" || continue # a failed disk that was unmounted
        if [[ $kind == bind ]]; then
            umount "$path" || failed+=("$path")
        else
            mount -o remount,rw "$path" || failed+=("$path")
        fi
    done < <(cat "$dir/mounts" 2>/dev/null)
    if ((${#failed[@]} > 0)); then
        echo "archive-lock: still read-only, the refusal stays: ${failed[*]}" >&2
        return 1
    fi
    # A snapperd started while locked (fix starts one) keeps refusing with EROFS after the
    # remount until it restarts (guest-script probe); it is started again on the next call.
    systemctl stop snapperd.service
    rm -f "$dir/mounts" "$dir/reason"
}

archive_lock_status() {
    local dir kind path state
    dir=$(_al_dir)
    if [[ ! -e $dir/reason ]]; then
        echo unlocked
        return 0
    fi
    echo locked
    echo "reason: $(<"$dir/reason")"
    while read -r kind path; do
        if ! _al_is_mount "$path"; then
            state=missing
        elif _al_readonly "$path"; then
            state=ro
        else
            state=rw
        fi
        echo "$kind $path $state"
    done < <(cat "$dir/mounts" 2>/dev/null)
}

# Options that take a value (SnapRAID 14.4 cmdline/snapraid.c, snapraid-btrfs 6492a45 usage):
# short option letters, and long option names.
_AL_SNAPRAID_SHORT="tcfdpoSBLilWCw"
_AL_SNAPRAID_LONG="conf filter filter-disk percentage plan older-than start count error-limit import
    log force-realloc-tail bw-limit tail gen-conf gui-threshold-removes gui-threshold-updates
    speed-test-period speed-test-disks-number speed-test-blocks-size test-force-scrub-at test-run
    test-import-content test-force-autosave-at test-io-cache test-parity-limit test-fmt"
_AL_SRB_SHORT="CUcdu"
_AL_SRB_LONG="conf cleanup debug-file xtrace-file description pool-dir pre-post no-pre-post
    snapper-path snapper-userdata snapraid-path use-snapshot use-snapshot-all snapper-configs
    snapper-configs-file"

# _al_command SHORT LONG ARGS...: the command word, the first argument that is neither an
# option nor the value of one.
_al_command() {
    local short=$1 long arg rest skip=0
    long=" ${2//$'\n'/ } "
    shift 2
    for arg in "$@"; do
        if ((skip)); then
            skip=0
            continue
        fi
        case $arg in
        -- | -) ;;
        --*) [[ $arg == *=* || $long != *" ${arg#--} "* ]] || skip=1 ;;
        -*)
            rest=${arg#-}
            while [[ -n $rest ]]; do
                if [[ $short == *"${rest:0:1}"* ]]; then
                    [[ -n ${rest:1} ]] || skip=1 # the value is the next argument
                    break
                fi
                rest=${rest:1}
            done
            ;;
        *)
            echo "$arg"
            return 0
            ;;
        esac
    done
}

# _al_wrap TOOL REAL RUN SHORT LONG ARGS...: anything but a RUN command is refused while locked.
_al_wrap() {
    local tool=$1 real=$2 run=" $3 " command
    command=$(_al_command "$4" "$5" "${@:6}")
    if [[ -z $command || $run != *" $command "* ]]; then
        archive_lock_guard "$tool ${command:-${*:6}}" || return
    fi
    exec "$real" "${@:6}"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -euo pipefail
    case ${1-} in
    lock | unlock | status) "archive_lock_$1" "${@:2}" ;;
    snapraid)
        _al_wrap snapraid "${ARCHIVE_LOCK_SNAPRAID:?the real snapraid}" \
            "$_AL_SNAPRAID_RUN" "$_AL_SNAPRAID_SHORT" "$_AL_SNAPRAID_LONG" "${@:2}"
        ;;
    snapraid-btrfs)
        _al_wrap snapraid-btrfs "${ARCHIVE_LOCK_SNAPRAID_BTRFS:?the real snapraid-btrfs}" \
            "$_AL_SRB_RUN" "$_AL_SRB_SHORT" "$_AL_SRB_LONG" "${@:2}"
        ;;
    *)
        echo "usage: archive-lock.sh lock REASON | unlock | status | snapraid ARGS | snapraid-btrfs ARGS" >&2
        exit 2
        ;;
    esac
fi
