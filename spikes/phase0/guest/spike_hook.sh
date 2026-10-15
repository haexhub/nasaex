#!/usr/bin/env bash
# spike_hook <point>: the blocking hook of the Phase 0 mover scaffolding. NOT product code.
#
# Protocol (specs/001-phase0-storage-spikes/contracts/mover-steps.md, "Hook protocol"):
#   - Unarmed (no file armed/<point> in the hook directory): return immediately.
#   - Armed: write reached/<point>, then block reading the FIFO go/<point>. The host decides
#     what happens while the process is blocked here: kill it, power the machine off, fail a
#     device, or write one line to the FIFO to let the run continue.
#   - After being released the hook removes armed/<point>, so a point passed a second time
#     (a retry) does not block unless the host arms it again.
# The hook directory is a tmpfs, so a power-off clears it. The hook never calls sync or
# fsync and never drops caches: an interruption sees exactly the effects of earlier steps.

spike_hook() {
    local point=$1
    local dir=${SPIKE_HOOK_DIR:-/run/spike-hooks}

    [[ -e $dir/armed/$point ]] || return 0

    mkdir -p "$dir/go" "$dir/reached" || return 1
    [[ -p $dir/go/$point ]] || mkfifo "$dir/go/$point" || return 1
    : >"$dir/reached/$point" || return 1
    read -r _ <"$dir/go/$point" || return 1
    rm -f "$dir/armed/$point"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    spike_hook "$@"
fi
