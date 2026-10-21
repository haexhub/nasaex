#!/usr/bin/env bash
# The corrupted-parity negative control of S2 (research D7, task T064). NOT product code.
#
#   corrupted-parity.sh
#
# Overwrites every byte of every parity file of the SnapRAID config (SNAPRAID_CONF, default
# /etc/snapraid.conf) in place with the pattern 0xA5, size unchanged, and syncs it to the disk.
# Run after a completed sync and before the disk failure of S2.1: the recovery then cannot
# reconstruct the failed disk's files, and S2.1 must judge them not bit-exact. If S2.1 passes
# against this control, the harness is wrong. Every parity file is checked first (a regular,
# non-empty file), so a refusal leaves all of them unchanged; one line per file gives its sha256
# before and after, and a file whose hash did not change is refused (exit 1). While the archive
# is locked the parity filesystem is read-only and the write fails.
#
#: control: corrupted-parity
#: violates: recovery restores every file of the failed disk bit-exact as of the last completed sync
#: flagged-by: S2.1

set -euo pipefail

CONF=${SNAPRAID_CONF:-/etc/snapraid.conf}

refuse() {
    echo "corrupted-parity: refused: $*" >&2
    exit 1
}

if (($#)); then
    echo "usage: corrupted-parity.sh (no arguments; the config is SNAPRAID_CONF)" >&2
    exit 2
fi

mapfile -t parity < <(awk '$1 ~ /^([2-6]-)?parity$/ { print $NF }' "$CONF")
((${#parity[@]})) || refuse "no parity file in $CONF"
for file in "${parity[@]}"; do
    [[ -f $file && -s $file ]] || refuse "$file is not a non-empty regular file"
done

for file in "${parity[@]}"; do
    before=$(sha256sum <"$file" | cut -d' ' -f1)
    head -c "$(stat -c %s "$file")" /dev/zero | tr '\0' '\245' |
        dd of="$file" bs=1M iflag=fullblock conv=notrunc,fsync status=none
    after=$(sha256sum <"$file" | cut -d' ' -f1)
    [[ $after != "$before" ]] || refuse "$file is unchanged (sha256 $before)"
    echo "corrupted $file: sha256 $before -> $after"
done
