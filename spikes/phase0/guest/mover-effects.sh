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

# _data_extents SRC FROM TO: "<start> <end>" per data extent of SRC clipped to [FROM, TO),
# found with SEEK_DATA/SEEK_HOLE (no shell tool reports them; python3 is in the node closure).
# A filesystem without hole reporting returns the whole range as data.
_data_extents() {
    python3 - "$@" <<'PY'
import os, sys
src, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
fd = os.open(src, os.O_RDONLY)
offset = lo
while offset < hi:
    try:
        start = os.lseek(fd, offset, os.SEEK_DATA)
    except OSError:  # ENXIO: only a hole remains
        break
    if start >= hi:
        break
    offset = os.lseek(fd, start, os.SEEK_HOLE)
    print(start, min(offset, hi))
PY
}

# _copy_range SRC TMP FROM TO: write the data extents of SRC in [FROM, TO) into TMP at the same
# offsets and set the logical size of TMP to TO. Holes are never written, so they stay holes;
# written zeros stay data (no conv=sparse).
_copy_range() {
    local src=$1 tmp=$2 from=$3 to=$4 extents start end
    extents=$(_data_extents "$src" "$from" "$to") || return 1
    while read -r start end; do
        [[ -n $start ]] || continue
        dd if="$src" of="$tmp" bs=1M iflag=skip_bytes,count_bytes oflag=seek_bytes \
            skip="$start" seek="$start" count=$((end - start)) conv=notrunc status=none || return 1
    done <<<"$extents"
    truncate -s "$to" "$tmp"
}

# mover_copy_item SRC DEST FIRST
#   SRC:  the pool file (read through the pool branch)
#   DEST: the final path in the archive, written through the base pool
# Copies to a temporary name in the target directory, fsyncs, renames atomically over DEST (a
# replacement atomically replaces an older archived version) and fsyncs the directory. Mode,
# xattrs, mtime and the hole map of a sparse source are kept ("Metadata preservation"); further
# links of a copied pool inode are made by mover_link_item.
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

    _copy_range "$src" "$tmp" 0 "$half" || return 1
    _hook_first "$first" "mid:M1:tmp-partial"

    _copy_range "$src" "$tmp" "$half" "$size" || return 1
    touch -m -r "$src" "$tmp" || return 1
    sync "$tmp" || return 1
    _hook_first "$first" "mid:M1:tmp-data-synced"

    mv -T "$tmp" "$dest" || return 1
    _hook_first "$first" "mid:M1:renamed"

    sync "$dir" || return 1
    _hook_first "$first" "mid:M1:parent-synced"
    _hook_first "$first" "mid:M1:item-1-done"
}

# mover_link_item ARCHIVE_COPY DEST
#   ARCHIVE_COPY: the archive copy this run already made of the same pool inode
#   DEST:         the final archive path of a further link of that inode
# Links ARCHIVE_COPY to a temporary name beside DEST, renames it atomically over DEST and
# fsyncs the directory, so items that share an inode in the pool share one inode and the same
# link count in the archive. The caller (the reference mover) remembers, in memory and for this
# run only, which pool inode it copied to which archive path. No hook fires: item 1 is always
# copied, never linked. Whether a link through the base pool lands on the disk of ARCHIVE_COPY
# (mergerfs link behaviour) is UNVERIFIED and probed in the S3 shakedown.
mover_link_item() {
    local target=$1 dest=$2
    local dir tmp

    dir=$(dirname "$dest")
    tmp="$dir/.$(basename "$dest").tmp-$$"
    # Already one inode: a rename between two links of one file does nothing and would leave
    # the temporary name behind.
    [[ $target -ef $dest ]] && return 0
    mkdir -p "$dir" || return 1
    ln -T "$target" "$tmp" || return 1
    mv -T "$tmp" "$dest" || return 1
    sync "$dir"
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
