#!/usr/bin/env bash
# Shared move primitives of the Phase 0 scaffolding. NOT product code.
#
# The durable effects of mover steps M1 and M5 (contracts/mover-steps.md), each calling
# spike_hook at its named point. Reused by S1.10 (a file moved the mover's way), S2.6 (the mover
# action that the archive lock must refuse) and the reference mover of S3.
#
# Source this file or run it with a function name:  mover-effects.sh mover_copy_item SRC DEST 1
#
# Hooks fire only for the first item of a loop step (`first` = 1), like the point list says.

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
# shellcheck source-path=SCRIPTDIR source=spike_hook.sh
source "$HERE/spike_hook.sh"

_hook_first() { # <first> <point>
    [[ $1 == 1 ]] && spike_hook "$2"
    return 0
}

# mover_copy_item SRC DEST FIRST
#   SRC:  the pool file (read through the pool branch)
#   DEST: the final path in the archive, written through the base pool
# Copies to a temporary name in the target directory, fsyncs, renames atomically over DEST (a
# replacement atomically replaces an older archived version) and fsyncs the directory.
mover_copy_item() {
    local src=$1 dest=$2 first=${3:-0}
    local dir base tmp size half

    dir=$(dirname "$dest")
    base=$(basename "$dest")
    tmp="$dir/.$base.tmp-$$"
    size=$(stat -c %s "$src") || return 1
    half=$((size / 2))

    mkdir -p "$dir" || return 1
    # An empty file that already carries the mode and the extended attributes of the source.
    cp --attributes-only --preserve=mode,xattr "$src" "$tmp" || return 1
    _hook_first "$first" "mid:M1:tmp-created"

    head -c "$half" "$src" >"$tmp" || return 1
    _hook_first "$first" "mid:M1:tmp-partial"

    tail -c +"$((half + 1))" "$src" >>"$tmp" || return 1
    touch -m -r "$src" "$tmp" || return 1
    sync "$tmp" || return 1
    _hook_first "$first" "mid:M1:tmp-data-synced"

    mv -T "$tmp" "$dest" || return 1
    _hook_first "$first" "mid:M1:renamed"

    sync "$dir" || return 1
    _hook_first "$first" "mid:M1:parent-synced"
    _hook_first "$first" "mid:M1:item-1-done"
}

# mover_unlink_pool_copy PATH FIRST
# The removal of one pool copy of M5. The caller has validated the item (identity unchanged
# since M2, parity coverage verified); this primitive marks the points and unlinks.
mover_unlink_pool_copy() {
    local path=$1 first=${2:-0}

    _hook_first "$first" "mid:M5:item-validated"
    rm -f -- "$path" || return 1
    _hook_first "$first" "mid:M5:unlinked"
    _hook_first "$first" "mid:M5:item-1-done"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -euo pipefail
    "$@"
fi
