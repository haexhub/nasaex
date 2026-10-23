#!/usr/bin/env bash
# The archive lock of S2 (research D11, task T061). NOT product code.
#
#   archive-lock.sh lock REASON     refuse maintenance from now on, then remount read-only every
#                                   filesystem the SnapRAID config names: each archive data disk,
#                                   each parity disk and each content-file location (a content
#                                   directory on no locked filesystem gets a read-only bind mount
#                                   of itself, so the system disk stays writable)
#   archive-lock.sh open-for-resync the resync stage of the recovery (recover.sh): remount them
#                                   read-write and stop snapperd, but keep refusing everything
#                                   except the sync itself
#   archive-lock.sh unlock          remount them read-write, stop snapperd; the refusal is lifted
#                                   last
#   archive-lock.sh status          "locked" or "unlocked", the reason, one line per location
#   archive-lock.sh exempt DISK     while locked: the filesystem mounted at the data path of DISK
#                                   (the replacement of the failed disk, recover.sh step 3), by its
#                                   UUID, stays writable through every later lock and restore,
#                                   because fix writes to it; another filesystem there (the failed
#                                   disk back with the same label) is locked like the others
#   archive-lock.sh restore         at boot, before any timer (nix/archive-lock.nix): set the
#                                   saved lock again, ending a resync stage; without a saved lock,
#                                   lock an array that was synced (a content file exists) when a
#                                   data disk is not mounted
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
# to it, and stays so once exempt. The state lives on the system disk (/var/lib/archive-lock):
# a boot mounts every location read-write from the fstab, and `restore` locks them again before
# any timer runs (task T066a; probe-s2-boot measured that a lock in /run was gone after a boot).

_AL_EROFS=30
# The commands of each tool that run while locked: fix (it writes only to the disk it fixes, the
# replacement) and the ones that only read. Every other command is refused, unknown ones too.
_AL_SNAPRAID_RUN="check diff dup fix list locate status devices smart probe up down"
_AL_SRB_RUN="check config dup fix list locate ls status"

# The actions that the resync stage lets through: the sync, and the cleanup that snapsync sync runs
# once the new snapshot set is complete (it deletes the older sets).
_AL_RESYNC="snapsync sync|snapraid sync|snapraid-btrfs sync|snapraid-btrfs cleanup"

_al_dir() { echo "${ARCHIVE_LOCK_DIR:-/var/lib/archive-lock}"; }

# archive_lock_guard ACTION: return 0 when unlocked, else print the refusal and return 30.
archive_lock_guard() {
    local dir
    dir=$(_al_dir)
    [[ -e $dir/reason ]] || return 0
    if [[ -e $dir/resync && "|$_AL_RESYNC|" == *"|$1|"* ]]; then
        return 0
    fi
    echo "archive-lock: refused: $1: the archive is locked ($(<"$dir/reason"))" >&2
    return "$_AL_EROFS"
}

# _al_paths REGEX: the last field of every SnapRAID config line whose keyword matches.
_al_paths() {
    awk -v re="$1" '$1 ~ re { print $NF }' "${SNAPRAID_CONF:-/etc/snapraid.conf}"
}

# _al_locations REGEX: "<data disk name, or -> <path>" of every config line whose keyword matches.
_al_locations() {
    awk -v re="$1" '$1 ~ re { print ($1 == "data" ? $2 : "-"), $NF }' \
        "${SNAPRAID_CONF:-/etc/snapraid.conf}"
}

# _al_exempt DISK TARGET: TARGET is mounted from the filesystem that `exempt DISK` recorded.
_al_exempt() {
    local uuid
    uuid=$(findmnt -n -o UUID --mountpoint "$2") || return 1
    grep -qxF "$1 $uuid" "$(_al_dir)/exempt" 2>/dev/null
}

# _al_mount_of PATH: the mount that holds PATH; for a path that does not exist (the parity file of
# a parity disk that is not mounted: findmnt exits 1 without a word) the one of its parent.
_al_mount_of() {
    local path=$1
    until findmnt -n -o TARGET --target "$path"; do
        [[ $path != / ]] || return 1
        path=$(dirname "$path")
    done
}

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
    local dir name path target first kind options failed=()
    local -A locked=()
    dir=$(_al_dir)

    mkdir -p "$dir"
    echo "$reason" >"$dir/reason" # the refusal holds from here on, also if a remount fails
    first=$(_al_paths '^content$' | head -n 1)
    if [[ -n $first && ! -e $first.lock ]]; then
        touch "$first.lock" # an existing one may already sit on a read-only location
    fi
    sync

    while read -r name path; do
        target=$(_al_mount_of "$path")
        if [[ $target == / ]]; then
            echo "$path: not mounted, skipped"
            continue
        fi
        if _al_exempt "$name" "$target"; then
            echo "$path: exempt, left writable"
            continue
        fi
        if grep -q "^$name " "$dir/exempt" 2>/dev/null; then
            echo "$path: not the filesystem exempt as the replacement of $name, locked"
        fi
        [[ -z ${locked[$target]-} ]] || continue
        locked[$target]=1
        _al_record remount "$target"
        _al_readonly "$target" || mount -o remount,ro "$target" || failed+=("$target")
    done < <(_al_locations '^(data|([2-6z]-)?parity)$')

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

# _al_release: every locked location read-write again and snapperd stopped; 1 if one stays
# read-only. A snapperd started while locked (fix starts one) keeps refusing with EROFS after the
# remount until it restarts (guest-script probe); the next snapper call starts a fresh one.
_al_release() {
    local dir kind path failed=()
    dir=$(_al_dir)
    while read -r kind path; do
        _al_is_mount "$path" || continue # a failed disk that was unmounted, a lifted bind
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
    systemctl stop snapperd.service
}

archive_lock_unlock() {
    local dir
    dir=$(_al_dir)
    [[ -e $dir/reason ]] || return 0
    _al_release || return 1
    rm -f "$dir/mounts" "$dir/resync" "$dir/exempt" "$dir/reason"
}

archive_lock_exempt() {
    local disk=${1:?usage: archive-lock.sh exempt DISK} dir path uuid
    dir=$(_al_dir)
    if [[ ! -e $dir/reason ]]; then
        echo "archive-lock: not locked; only the replacement of a locked array is exempt" >&2
        return 1
    fi
    path=$(_al_locations '^data$' | awk -v d="$disk" '$1 == d { print $2 }')
    if [[ -z $path ]]; then
        echo "archive-lock: no data disk $disk in the SnapRAID config" >&2
        return 1
    fi
    uuid=$(findmnt -n -o UUID --mountpoint "${path%/}") || {
        echo "archive-lock: nothing is mounted at $path; mount the replacement first" >&2
        return 1
    }
    { grep -v "^$disk " "$dir/exempt" 2>/dev/null || true; } >"$dir/exempt.new"
    echo "$disk $uuid" >>"$dir/exempt.new"
    mv "$dir/exempt.new" "$dir/exempt"
    sync "$dir/exempt" "$dir" # a power loss right after must not lose it
}

# A content file exists: the array was synced at least once, so its parity is worth protecting.
_al_synced() {
    local path
    while read -r path; do
        [[ ! -e $path ]] || return 0
    done < <(_al_paths '^content$')
    return 1
}

archive_lock_restore() {
    local dir name path missing=()
    dir=$(_al_dir)
    if [[ -e $dir/reason ]]; then
        rm -f "$dir/resync" # a boot ends the resync stage; recover.sh resync opens it again
        archive_lock_lock "$(<"$dir/reason")"
        return
    fi
    while read -r name path; do
        [[ $(_al_mount_of "$path") != / ]] || missing+=("$name")
    done < <(_al_locations '^data$')
    if ((${#missing[@]} == 0)) || ! _al_synced; then
        return 0
    fi
    if ((${#missing[@]} == 1)); then
        archive_lock_lock "boot: ${missing[0]} is not mounted"
    else
        archive_lock_lock "boot: ${missing[*]} are not mounted"
    fi
}

archive_lock_open_for_resync() {
    local dir
    dir=$(_al_dir)
    if [[ ! -e $dir/reason ]]; then
        echo "archive-lock: not locked; the resync stage follows a lock" >&2
        return 1
    fi
    _al_release || return 1
    touch "$dir/resync"
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
    if [[ -e $dir/resync ]]; then
        echo "stage: resync"
    fi
    [[ ! -e $dir/exempt ]] || sed 's/^/exempt: /' "$dir/exempt"
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
    lock | unlock | status | exempt | restore) "archive_lock_$1" "${@:2}" ;;
    open-for-resync) archive_lock_open_for_resync ;;
    snapraid)
        _al_wrap snapraid "${ARCHIVE_LOCK_SNAPRAID:?the real snapraid}" \
            "$_AL_SNAPRAID_RUN" "$_AL_SNAPRAID_SHORT" "$_AL_SNAPRAID_LONG" "${@:2}"
        ;;
    snapraid-btrfs)
        _al_wrap snapraid-btrfs "${ARCHIVE_LOCK_SNAPRAID_BTRFS:?the real snapraid-btrfs}" \
            "$_AL_SRB_RUN" "$_AL_SRB_SHORT" "$_AL_SRB_LONG" "${@:2}"
        ;;
    *)
        echo "usage: archive-lock.sh lock REASON | open-for-resync | unlock | status" \
            "| exempt DISK | restore | snapraid ARGS | snapraid-btrfs ARGS" >&2
        exit 2
        ;;
    esac
fi
