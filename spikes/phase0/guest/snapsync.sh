#!/usr/bin/env bash
# The snapshot-supported sync and fix of S2 around snapraid-btrfs (task T060). NOT product code.
#
#   snapsync.sh sync [--force-empty] [--log FILE] [-- SNAPRAID-OPTIONS...]
#   snapsync.sh fix DISK [--log FILE]
#   snapsync.sh complete
#   snapsync.sh covered [--plain] [--log FILE]
#   snapsync.sh free DISK
#
# sync      New read-only snapshots of every data disk, SnapRAID syncs them, and on exit 0
#           snapraid-btrfs marks the set `synced`. Only when the newest set of every disk says so
#           does `cleanup` delete the older sets: a set is kept until the next sync completes. A
#           failed sync keeps every snapshot. The force-empty override `-E` is passed only through
#           --force-empty, never among the extra options. Refused while the archive is locked.
# fix       After a disk replacement: stops snapperd first (while a config stays loaded it keeps
#           the old disk's snapshot list, probe T057), then fixes DISK on its live filesystem and
#           reads the other disks from their snapshots. The next step is a new sync; there is no
#           resume here (it fails on a replaced disk, probe T057).
# complete  Snapshot completion: the newest snapraid-btrfs snapshot of every config is `synced`.
# covered   The synced-set coverage check of M4, separate from completion: SnapRAID `diff` with
#           the config of the synced set (--plain: the live config) exits 0 with
#           summary:exit:equal and content_info:block_unsynced:0.
# free      Available bytes of DISK once btrfs has cleaned the deleted snapshots (`btrfs subvolume
#           sync`); right after a cleanup the freed space is not visible yet (probe T057).
#
# snapraid-btrfs always runs with LC_ALL=C (in a UTF-8 locale snapper prints tables it cannot
# parse) and --noninteractive; this wrapper never runs `-u sync diff` (it rewrites the `synced`
# userdata) or `dsync` (it passes -E). Spec Clarifications 2026-10-20 (S2 SnapRAID probe).

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
# shellcheck source-path=SCRIPTDIR source=archive-lock.sh
source "$HERE/archive-lock.sh"

CONF=${SNAPRAID_CONF:-/etc/snapraid.conf}
USAGE="usage: snapsync.sh sync [--force-empty] [--log FILE] [-- OPTIONS] | fix DISK [--log FILE]
       | complete | covered [--plain] [--log FILE] | free DISK"

# The newest snapshot that carries snapraid-btrfs userdata, from `snapper --jsonout list`:
# "<number> <value>", or "none".
NEWEST='
import json, sys
text = sys.stdin.read()
snapshots = next(iter(json.loads(text[text.index("{"):]).values()), [])
marked = [s for s in snapshots if (s.get("userdata") or {}).get("snapraid-btrfs")]
newest = max(marked, key=lambda s: s["number"], default=None)
if newest:
    print(newest["number"], newest["userdata"]["snapraid-btrfs"])
else:
    print("none")
'

_srb() { env LC_ALL=C snapraid-btrfs -c "$CONF" --noninteractive "$@"; }

_configs() {
    if [[ -n ${SNAPSYNC_CONFIGS-} ]]; then
        echo "$SNAPSYNC_CONFIGS"
    else
        # shellcheck source=/dev/null
        (source /etc/sysconfig/snapper && echo "$SNAPPER_CONFIGS")
    fi
}

_new_log() {
    local dir=${SNAPSYNC_LOG_DIR:-/var/log/snapsync}
    mkdir -p "$dir"
    echo "$dir/$1-$(date +%Y%m%dT%H%M%S%N).log"
}

_usage() {
    echo "$USAGE" >&2
    return 2
}

snapsync_sync() {
    local force=() log='' extra=() arg option rc=0
    while (($# > 0)); do
        case $1 in
        --force-empty) force=(-E) && shift ;;
        --log) log=$2 && shift 2 ;;
        --) extra=("${@:2}") && break ;;
        *) _usage || return ;;
        esac
    done
    for arg in "${extra[@]}"; do
        option=${arg%%=*}
        if [[ ($option == --force-e* && --force-empty == "$option"*) ||
            ($option == -[!-]* && $option == *E*) ]]; then
            echo "snapsync: the force-empty override is passed only as --force-empty" >&2
            return 2
        fi
    done
    archive_lock_guard "snapsync sync" || return
    [[ -n $log ]] || log=$(_new_log sync)

    _srb sync "${force[@]}" -l "$log" "${extra[@]}" || rc=$?
    if ((rc != 0)); then
        echo "snapsync: sync exited $rc; every snapshot is kept" >&2
        return "$rc"
    fi
    if ! snapsync_complete; then
        echo "snapsync: sync exited 0, but the set is not complete; nothing is cleaned up" >&2
        return 1
    fi
    _srb cleanup
}

snapsync_fix() {
    local disk=${1:?$USAGE} log=''
    shift
    while (($# > 0)); do
        case $1 in
        --log) log=$2 && shift 2 ;;
        *) _usage || return ;;
        esac
    done
    [[ -n $log ]] || log=$(_new_log "fix-$disk")
    systemctl stop snapperd.service
    _srb fix -d "$disk" -l "$log"
}

snapsync_complete() {
    local config configs newest number value incomplete=0
    configs=$(_configs) || configs=''
    if [[ -z ${configs//[[:space:]]/} ]]; then
        echo "snapsync: no snapper config; the set is not complete" >&2
        return 1
    fi
    for config in $configs; do
        newest=$(env LC_ALL=C snapper --jsonout -c "$config" list --disable-used-space |
            python3 -c "$NEWEST") || newest=none
        read -r number value <<<"$newest"
        if [[ $number == none ]]; then
            echo "$config: no snapraid-btrfs snapshot" >&2
            incomplete=1
        elif [[ $value != synced ]]; then
            echo "$config: newest snapraid-btrfs snapshot $number is $value" >&2
            incomplete=1
        else
            echo "$config: snapshot $number synced"
        fi
    done
    return "$incomplete"
}

snapsync_covered() {
    local plain=0 log='' conf rc=0 summary unsynced
    while (($# > 0)); do
        case $1 in
        --plain) plain=1 && shift ;;
        --log) log=$2 && shift 2 ;;
        *) _usage || return ;;
        esac
    done
    [[ -n $log ]] || log=$(_new_log diff)
    if ((plain)); then
        conf=$CONF
    else
        conf=${log%.log}.conf
        if ! _srb -u sync config >"$conf"; then
            echo "snapsync: not covered: no config of a synced set" >&2
            return 1
        fi
    fi
    snapraid -c "$conf" -l "$log" diff >/dev/null || rc=$?
    summary=$(sed -n 's/^summary:exit://p' "$log")
    unsynced=$(sed -n 's/^content_info:block_unsynced://p' "$log")
    if ((rc == 0)) && [[ $summary == equal && $unsynced == 0 ]]; then
        echo covered
        return 0
    fi
    echo "snapsync: not covered: diff exit $rc, summary:exit:${summary:-?}," \
        "content_info:block_unsynced:${unsynced:-?}" >&2
    return 1
}

snapsync_free() {
    local path=${SNAPSYNC_ARCHIVE_ROOT:-/mnt/archive}/${1:?$USAGE}
    btrfs subvolume sync "$path" >/dev/null
    df --output=avail -B1 "$path" | tail -n 1 | tr -d ' '
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -euo pipefail
    case ${1-} in
    sync | fix | complete | covered | free) "snapsync_$1" "${@:2}" ;;
    *) _usage ;;
    esac
fi
