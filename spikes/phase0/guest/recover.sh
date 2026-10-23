#!/usr/bin/env bash
# The safe recovery and resynchronisation sequence of S2 (task T062). NOT product code.
#
# One subcommand per step, run in this order. Every step ends with its own check and exits
# non-zero when the check fails, so no step runs on top of a failed one:
#
#   recover.sh detect DISK
#   recover.sh lock DISK
#   recover.sh replace DISK DEVICE FAILED_BYTES
#   recover.sh recover DISK [--plain]
#   recover.sh verify DISK [--accept-unrecoverable]
#   recover.sh resync DISK [--plain] [--accept-unrecoverable]
#   recover.sh unlock DISK
#
# The shape is the SnapRAID manual's recovery (research 3.4): stop all changes (lock), give the
# disk a new place (replace), `fix -d`, `check -a -d`, `sync`. The written procedure is rendered
# from the `#:` lines below by testlib/procedure.py, so text and commands cannot drift apart.
# --plain is the array without the snapshot workflow. recover exits 3 when it lists unrecoverable
# files; verify and resync then go on only with --accept-unrecoverable. State between steps:
# /var/lib/recover/<DISK>, on the system disk like the lock, so a boot between two steps keeps
# both (task T066a); logs: /var/log/recover. Reused by S3.4 and S4.6.

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
# shellcheck source-path=SCRIPTDIR source=archive-lock.sh
source "$HERE/archive-lock.sh"

CONF=${SNAPRAID_CONF:-/etc/snapraid.conf}
ARCHIVE=${RECOVER_ARCHIVE_ROOT:-/mnt/archive}
CONTENT_ROOT=${RECOVER_CONTENT_ROOT:-/mnt/archive-content}
LABELS=${RECOVER_LABEL_DIR:-/dev/disk/by-label}
STATE_ROOT=${RECOVER_STATE_DIR:-/var/lib/recover}
LOG_DIR=${RECOVER_LOG_DIR:-/var/log/recover}
USAGE="usage: recover.sh detect|lock|recover|verify|resync|unlock DISK [OPTIONS]
       | replace DISK DEVICE FAILED_BYTES"

say() { echo "recover.sh: $*"; }
fail() {
    echo "recover.sh: check failed: $*" >&2
    return 1
}
_state() {
    mkdir -p "$STATE_ROOT/$1"
    echo "$STATE_ROOT/$1"
}
_log() {
    mkdir -p "$LOG_DIR"
    echo "$LOG_DIR/$1-$2-$(date +%Y%m%dT%H%M%S%N).log"
}
# _replacement DISK: step 3 passed, and the data path of DISK is mounted from the filesystem it
# exempted (probe-s2-boot: a boot mounted the failed disk, back with the same label, instead).
_replacement() {
    [[ -e $STATE_ROOT/$1/replaced ]] || fail "run step 3 first" || return
    _al_exempt "$1" "$ARCHIVE/$1" || fail "$ARCHIVE/$1 is not mounted from the replacement of" \
        "step 3; take the failed disk out and boot again" || return
}
_replace_cleanup() {
    local disk=$1 path cleanup_failed=0
    for path in "$CONTENT_ROOT/$disk" "$ARCHIVE/$disk"; do
        _al_is_mount "$path" || continue
        if ! umount "$path" && ! umount -l "$path"; then
            cleanup_failed=1
        fi
    done
    return "$cleanup_failed"
}

#: 1. detect DISK
#: Do: Look for evidence that DISK failed: its data filesystem is not mounted, no device carries
#: its label, btrfs counts device errors on it (`btrfs device stats --check`), or a direct read of
#: its device fails (right after a failure the counters can still be 0 and reads come from the
#: page cache).
#: Check: At least one piece of evidence is found. Without any the procedure stops: a disk that
#: shows no failure is never recovered.
recover_detect() {
    local disk=$1 mount=$ARCHIVE/$1 device evidence=() state
    state=$(_state "$disk")
    # A new detection starts a new recovery attempt. Do not let a completed attempt authorize
    # verify or resync after the operator has unlocked the array.
    rm -f "$state/unrecoverable" "$state/verified"
    _al_is_mount "$mount" || evidence+=("$mount is not mounted")
    [[ -e $LABELS/$disk ]] || evidence+=("no device carries the label $disk")
    if _al_is_mount "$mount"; then
        btrfs device stats --check "$mount" >/dev/null ||
            evidence+=("btrfs counts device errors on $mount")
        device=$(findmnt -n -o SOURCE --mountpoint "$mount")
        device=${device%%[*}
        dd if="$device" of=/dev/null bs=4096 count=1 iflag=direct status=none 2>/dev/null ||
            evidence+=("a direct read of $device fails")
    fi
    if ((${#evidence[@]} == 0)); then
        fail "$disk shows no failure; nothing is recovered"
        return
    fi
    say "$disk failed:"
    printf '  %s\n' "${evidence[@]}"
}

#: 2. lock DISK
#: Do: Unmount DISK (lazily if a process still holds it), then `archive-lock lock "DISK failed"`:
#: sync, scrub and every mover action are refused, and the data filesystems of the other disks,
#: the parity filesystem and the content locations turn read-only.
#: Check: `archive-lock status` says locked and no location it lists is writable.
recover_lock() {
    local disk=$1 mount kind path state
    for mount in "$ARCHIVE/$disk" "$CONTENT_ROOT/$disk"; do
        _al_is_mount "$mount" || continue
        umount "$mount" || umount -l "$mount" || fail "cannot unmount $mount" || return
    done
    archive_lock_lock "$disk failed" || fail "the lock is incomplete" || return
    while read -r kind path state; do
        [[ $state != rw ]] || fail "$kind $path is still writable" || return
    done < <(archive_lock_status | tail -n +3)
    say "locked: the archive refuses every change"
}

#: 3. replace DISK DEVICE FAILED_BYTES
#: Do: Refuse DEVICE if the archive is not locked, if DEVICE is smaller than FAILED_BYTES (the
#: failed disk's size from the disk inventory) or if it carries any filesystem signature; never
#: guess. Then create a btrfs filesystem labelled DISK on DEVICE with the subvolumes `data` and
#: `content`, mount `data` at the data path of DISK and `content` beside it, create the
#: directories `branch` and `trash` and the `.snapshots` subvolume (`spike-snapshots-init`),
#: write the new filesystem to the disk (`sync -f`, a crash must not take the layout), and exempt
#: it from the lock by its UUID (`archive-lock exempt DISK`), so the replacement stays writable
#: through every later lock, also the one a boot sets again.
#: Check: The data path is mounted from DEVICE, DEVICE carries the label DISK, `branch` is empty,
#: `.snapshots` is a btrfs subvolume and DISK is exempt.
recover_replace() {
    local disk=$1 device=${2:?$USAGE} failed_bytes=${3:?$USAGE} size tmp source real state
    state=$(_state "$disk")
    rm -f "$state/replaced"
    [[ -e $(_al_dir)/reason ]] || {
        fail "the archive is not locked; replace only after step 2"
        return
    }
    size=$(blockdev --getsize64 "$device") || {
        fail "cannot read the size of $device"
        return
    }
    ((size >= failed_bytes)) ||
        fail "$device holds $size bytes, less than the $failed_bytes of the failed disk" || return
    if blkid -p "$device" >/dev/null 2>&1; then
        fail "$device already carries a filesystem signature; use a blank disk"
        return
    fi
    mkfs.btrfs -q -L "$disk" "$device" || {
        fail "mkfs.btrfs on $device"
        return
    }
    udevadm settle
    tmp=$(mktemp -d) || {
        fail "temporary mount directory"
        return
    }
    if ! mount "$device" "$tmp" ||
        ! btrfs subvolume create "$tmp/data" >/dev/null ||
        ! btrfs subvolume create "$tmp/content" >/dev/null ||
        ! umount "$tmp"; then
        umount "$tmp" 2>/dev/null || umount -l "$tmp" 2>/dev/null || true
        rmdir "$tmp" 2>/dev/null || true
        fail "subvolumes on $device"
        return
    fi
    rmdir "$tmp" || {
        fail "temporary mount directory"
        return
    }
    mkdir -p "$ARCHIVE/$disk" "$CONTENT_ROOT/$disk"
    if ! mount -o subvol=data "$device" "$ARCHIVE/$disk" ||
        ! mount -o subvol=content "$device" "$CONTENT_ROOT/$disk" ||
        ! mkdir "$ARCHIVE/$disk/branch" "$ARCHIVE/$disk/trash" ||
        ! spike-snapshots-init "$disk"; then
        _replace_cleanup "$disk" || true
        fail "the layout of $disk"
        return
    fi

    source=$(findmnt -n -o SOURCE --mountpoint "$ARCHIVE/$disk") || {
        _replace_cleanup "$disk" || true
        fail "not mounted"
        return
    }
    real=$(readlink -f "$device")
    if [[ ${source%%[*} != "$real" ]]; then
        _replace_cleanup "$disk" || true
        fail "$ARCHIVE/$disk is mounted from $source"
        return
    fi
    if [[ $(blkid -s LABEL -o value "$device") != "$disk" ]]; then
        _replace_cleanup "$disk" || true
        fail "label of $device"
        return
    fi
    if [[ -n $(ls -A "$ARCHIVE/$disk/branch") ]]; then
        _replace_cleanup "$disk" || true
        fail "branch is not empty"
        return
    fi
    if ! btrfs subvolume show "$ARCHIVE/$disk/.snapshots" >/dev/null; then
        _replace_cleanup "$disk" || true
        fail ".snapshots is no subvolume"
        return
    fi
    sync -f "$ARCHIVE/$disk" || {
        _replace_cleanup "$disk" || true
        fail "write the filesystem of $disk to $device"
        return
    }
    archive_lock_exempt "$disk" || {
        _replace_cleanup "$disk" || true
        fail "exempt $disk from the lock"
        return
    }
    touch "$state/replaced"
    # The whole system filesystem: the marker and the directories _state may just have created.
    sync -f "$state/replaced" # a power loss right after must lose neither
    say "$device replaces $disk"
}

#: 4. recover DISK [--plain]
#: Do: Refuse unless the data path of DISK is mounted from the filesystem exempt in step 3 (a boot
#: mounts the failed disk instead when it is back with the same label). Restore the files of DISK
#: as of the last completed sync: `snapsync fix DISK` (DISK from its live filesystem, the other
#: disks from their synced snapshots; snapperd is stopped first), or `snapraid -d DISK -l LOG
#: fix` without the snapshot workflow. The fix may be retried, for
#: example with copies of deleted files (`-i DIR`); after a sync it can no longer be.
#: Check: The fix log carries its `summary:exit` tag. Every file the log reports
#: `status:unrecoverable` is listed with this guidance, and the step exits 3: restore those files
#: from elsewhere or retry the fix now, because the resync makes the loss final.
recover_recover() {
    local disk=$1 plain=0 log rc=0 state
    [[ ${2-} == --plain ]] && plain=1
    state=$(_state "$disk")
    _replacement "$disk" || return
    log=$(_log fix "$disk")
    if ((plain)); then
        snapraid -c "$CONF" -d "$disk" -l "$log" fix || rc=$?
    else
        bash "$HERE/snapsync.sh" fix "$disk" --log "$log" || rc=$?
    fi
    grep -q '^summary:exit:' "$log" 2>/dev/null || fail "no summary in $log (fix exit $rc)" || return
    sed -n "s/^status:unrecoverable:$disk://p" "$log" | sort -u >"$state/unrecoverable"
    rm -f "$state/verified"
    if [[ -s $state/unrecoverable ]]; then
        echo "recover.sh: unrecoverable files of $disk (renamed *.unrecoverable by the fix):"
        sed 's/^/  /' "$state/unrecoverable"
        echo "recover.sh: restore them from elsewhere, or retry the fix now (for example with -i" \
            "and copies of files deleted since the last sync); a resync makes the loss final"
        return 3
    fi
    ((rc == 0)) || fail "fix exit $rc without a listed unrecoverable file" || return
    say "every file of $disk recovered"
}

#: 5. verify DISK [--accept-unrecoverable]
#: Do: Refuse as step 4 does unless the replacement is mounted. `snapraid -d DISK -a -l LOG
#: check`: every file of DISK against its hash as of the last sync, without using parity.
#: Check: The check exits 0. With files listed by step 4, it may fail only on those files, and only
#: when the loss is accepted with --accept-unrecoverable.
recover_verify() {
    local disk=$1 accept=0 log rc=0 state failing
    [[ ${2-} == --accept-unrecoverable ]] && accept=1
    state=$(_state "$disk")
    [[ -e $state/unrecoverable ]] || fail "run step 4 first" || return
    _replacement "$disk" || return
    log=$(_log check "$disk")
    snapraid -c "$CONF" -d "$disk" -a -l "$log" check || rc=$?
    if ((rc != 0)); then
        failing=$(sed -n -E "s/^(status:(unrecoverable|recoverable)|[a-z_]*error[a-z_]*:[0-9]+):$disk:([^:]*).*/\\3/p" "$log" | sort -u)
        [[ -n $failing ]] || fail "check exit $rc names no file" || return
        if comm -23 <(echo "$failing") "$state/unrecoverable" | grep -q .; then
            fail "files fail the check that step 4 did not list:" \
                "$(comm -23 <(echo "$failing") "$state/unrecoverable" | tr '\n' ' ')"
            return
        fi
        ((accept)) || fail "unrecoverable files remain; accept the loss with" \
            "--accept-unrecoverable or retry step 4" || return
    fi
    touch "$state/verified"
    say "$disk verified against the hashes of the last sync"
}

#: 6. resync DISK [--plain] [--accept-unrecoverable]
#: Do: Only after step 5 passed and while the replacement is mounted (as in step 4):
#: `archive-lock open-for-resync` (the filesystems turn writable, snapperd is restarted, every
#: action except the sync is still refused), then `snapsync sync`, or `snapraid -l LOG sync`
#: without the snapshot workflow.
#: Check: The sync exits 0 and the array is covered again: `snapsync complete` and `snapsync
#: covered` (without the snapshot workflow `snapsync covered --plain`).
recover_resync() {
    local disk=$1 plain=0 accept=0 state log arg
    for arg in "${@:2}"; do
        case $arg in
        --plain) plain=1 ;;
        --accept-unrecoverable) accept=1 ;;
        *) echo "$USAGE" >&2 && return 2 ;;
        esac
    done
    state=$(_state "$disk")
    [[ -e $state/verified ]] || fail "step 5 has not passed" || return
    if [[ -s $state/unrecoverable ]] && ((!accept)); then
        fail "unrecoverable files are listed; a resync makes their loss final"
        return
    fi
    _replacement "$disk" || return
    archive_lock_open_for_resync || fail "the resync stage" || return
    log=$(_log sync "$disk")
    if ((plain)); then
        snapraid -c "$CONF" -l "$log" sync || fail "sync, log $log" || return
        bash "$HERE/snapsync.sh" covered --plain || fail "the array is not covered" || return
    else
        bash "$HERE/snapsync.sh" sync --log "$log" || fail "sync, log $log" || return
        bash "$HERE/snapsync.sh" complete || fail "the snapshot set is not complete" || return
        bash "$HERE/snapsync.sh" covered || fail "the array is not covered" || return
    fi
    # Resync is the terminal data step. Clear its authorization before unlock so this recovery
    # attempt cannot be replayed from stale state if the operator pauses or reboots here.
    rm -f "$state/unrecoverable" "$state/verified"
    say "the array is synchronised and covered again"
}

#: 7. unlock DISK
#: Do: `archive-lock unlock`: the archive accepts changes again and the refusal is lifted.
#: Check: `archive-lock status` says unlocked and every data, parity and content location of the
#: array is writable.
recover_unlock() {
    local path target state
    archive_lock_unlock || fail "the unlock" || return
    [[ $(archive_lock_status) == unlocked ]] || fail "still locked" || return
    while read -r path; do
        target=$(_al_mount_of "$path")
        ! _al_readonly "$target" || fail "$target is still read-only" || return
    done < <(_al_paths '^(data|([2-6z]-)?parity|content)$')
    state=$(_state "$1")
    rm -f "$state/unrecoverable" "$state/verified"
    say "unlocked"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -uo pipefail
    case ${1-} in
    detect | lock | replace | recover | verify | resync | unlock)
        [[ -n ${2-} ]] || {
            echo "$USAGE" >&2
            exit 2
        }
        "recover_$1" "${@:2}"
        ;;
    *)
        echo "$USAGE" >&2
        exit 2
        ;;
    esac
fi
