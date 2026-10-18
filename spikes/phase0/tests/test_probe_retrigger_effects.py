"""The side-effect probe of the pool retrigger unit (vm-tests/probes/probe_retrigger_effects.py):
parsing of the guest reader and mount logs, the event and journal counts per boot, the summary
over the boots, the delay steps of the sweep and the QEMU options that attach a pool member.
The boots themselves need the VM."""

import pytest
from probes.pool_race import parse_monitor
from probes.retrigger_effects import (
    activation_order,
    attach_commands,
    attach_qemu_opts,
    count_lines,
    count_mount_events,
    count_uevents,
    monitor_reply,
    parse_mount_log,
    parse_reader_log,
    parse_sweep,
    reader_errors_after_ok,
    summarize_effects,
)

READER_LOG = """\
2.100000 /mnt/cache/probe-t051g/reader.bin ENOENT
2.100100 fd:/mnt/cache/probe-t051g/reader.bin ENOENT
5.300000 /mnt/cache/probe-t051g/reader.bin ok
5.300100 fd:/mnt/cache/probe-t051g/reader.bin ok
7.000000 heartbeat 250
7.400000 /mnt/cache/probe-t051g/reader.bin EIO
7.420000 /mnt/cache/probe-t051g/reader.bin ok
"""


def test_parse_reader_log_splits_transitions_and_heartbeats():
    log = parse_reader_log(READER_LOG + "garbage line\n")
    assert len(log["transitions"]) == 6
    assert log["transitions"][2] == {
        "t": 5.3,
        "key": "/mnt/cache/probe-t051g/reader.bin",
        "outcome": "ok",
    }
    assert log["heartbeats"] == [(7.0, 250)]


def test_reader_errors_after_ok_ignore_the_errors_before_the_mount():
    transitions = parse_reader_log(READER_LOG)["transitions"]
    errors = reader_errors_after_ok(transitions)
    assert errors == [{"t": 7.4, "key": "/mnt/cache/probe-t051g/reader.bin", "outcome": "EIO"}]


def test_reader_errors_after_ok_report_a_key_that_never_read_ok_nothing():
    log = parse_reader_log("1.0 /mnt/cache/x ENOENT\n9.0 /mnt/cache/x ENOTCONN\n")
    transitions = log["transitions"]
    assert len(transitions) == 2
    assert reader_errors_after_ok(transitions) == []


MOUNT_LOG = """\
4.900000 mount 61 /mnt/pool btrfs /dev/vdb[/pool]
5.100000 mount 70 /mnt/cache fuse.mergerfs cachepool
9.000000 umount 70 /mnt/cache fuse.mergerfs cachepool
9.200000 mount 75 /mnt/cache fuse.mergerfs cachepool
"""


def test_parse_mount_log_and_count_per_target():
    events = parse_mount_log(MOUNT_LOG)
    assert events[0] == {
        "t": 4.9,
        "action": "mount",
        "id": 61,
        "target": "/mnt/pool",
        "fstype": "btrfs",
        "source": "/dev/vdb[/pool]",
    }
    assert count_mount_events(events) == {
        "/mnt/pool": {"mount": 1},
        "/mnt/cache": {"mount": 2, "umount": 1},
    }
    assert count_mount_events(events, since=8.0) == {"/mnt/cache": {"mount": 1, "umount": 1}}


MONITOR = """\
KERNEL[4.80] add      /devices/virtio7/block/vdb (block)
SEQNUM=1

UDEV  [5.20] add      /devices/virtio7/block/vdb (block)
SEQNUM=1
ID_BTRFS_READY=0

KERNEL[6.00] add      /devices/virtio7/block/vdb (block)
SEQNUM=9
SYNTH_UUID=0

UDEV  [6.10] add      /devices/virtio7/block/vdb (block)
SEQNUM=9
ID_BTRFS_READY=1

UDEV  [6.20] change   /devices/virtio9/block/vdd (block)
SEQNUM=10
"""


def test_count_uevents_per_device_and_inside_a_window():
    events = parse_monitor(MONITOR)
    assert count_uevents(events) == {
        "vdb": {"KERNEL add": 2, "UDEV add": 2},
        "vdd": {"UDEV change": 1},
    }
    assert count_uevents(events, since=5.9, until=6.15) == {"vdb": {"KERNEL add": 1, "UDEV add": 1}}


def test_count_lines_counts_each_pattern_independently():
    lines = [
        (4.5, "server systemd[1]: Found device /dev/disk/by-label/spikepool."),
        (4.6, "server systemd[1]: Mounting /mnt/pool..."),
        (4.7, "server systemd[1]: Mounted /mnt/pool."),
        (6.0, "server systemd[1]: Found device /dev/disk/by-label/spikepool."),
    ]
    patterns = {"found": r"Found device", "mounted": r"Mounted /mnt/", "unmount": r"Unmount"}
    assert count_lines(lines, patterns) == {"found": 2, "mounted": 1, "unmount": 0}
    assert count_lines(lines, patterns, since=5.0) == {"found": 1, "mounted": 0, "unmount": 0}


def test_activation_order_sorts_by_time_and_leaves_out_inactive_units():
    times = {"mnt-pool.mount": 5.2, "local-fs.target": 6.0, "probe.service": 5.9, "x.mount": None}
    assert activation_order(times) == ["mnt-pool.mount", "probe.service", "local-fs.target"]


def _boot(index, up=True, pool=5.0, unit_result="success", reader_errors=(), found=1):
    return {
        "index": index,
        "up": up,
        "timings": {"start_to_multi_user_s": 20.0 + index, "start_to_no_jobs_s": 21.0 + index},
        "manager_finish_s": 8.0 + index,
        "active_s": {"mnt-pool.mount": pool, "multi-user.target": 7.5 + index},
        "unit": {"Result": unit_result, "ExecMainStatus": "0"},
        "journal_counts": {"found_device": found},
        "uevents": {"vdb": {"UDEV add": 2}},
        "reader_errors_after_ok": list(reader_errors),
        "mount_events": {"/mnt/pool": {"mount": 1}},
        "order": ["mnt-pool.mount", "multi-user.target"],
        "smbd": {"NRestarts": "0"},
        "pool_device_inactive_after_active": False,
    }


def test_summarize_effects_spreads_histograms_and_harm_indicators():
    boots = [
        _boot(0, pool=5.0),
        _boot(1, pool=6.0, found=2),
        _boot(2, up=False, pool=None, unit_result="exit-code", reader_errors=[{"t": 7.0}]),
    ]
    summary = summarize_effects(boots)
    assert summary["boots"] == 3
    assert summary["failed_boots"] == [2]
    assert summary["active_s"]["mnt-pool.mount"] == {
        "n": 2,
        "min": 5.0,
        "median": 5.5,
        "max": 6.0,
        "missing": 1,
    }
    assert summary["manager_finish_s"]["median"] == 9.0
    assert summary["host_start_to_no_jobs_s"]["max"] == 23.0
    assert summary["unit_results"] == {"exit-code/0": 1, "success/0": 2}
    assert summary["journal_counts"]["found_device"] == {"1": 2, "2": 1}
    assert summary["uevents"]["vdb"]["UDEV add"] == {"2": 3}
    assert summary["reader_errors_after_ok_boots"] == [2]
    assert summary["mount_events"]["/mnt/pool"]["mount"] == {"1": 3}
    assert summary["orders"] == {"mnt-pool.mount < multi-user.target": 3}
    assert summary["smbd_restarts"] == {"0": 3}
    assert summary["pool_device_inactive_after_active_boots"] == []


def test_summarize_effects_of_no_boot():
    assert summarize_effects([])["boots"] == 0


def test_parse_sweep_reads_not_ready_and_ready_delays():
    assert parse_sweep("1:2, 4:5") == [(1, 2), (4, 5)]


@pytest.mark.parametrize("spec", ["", "1", "1:x", "-1:2", "1:2:3", "1:2,,"])
def test_parse_sweep_rejects_malformed_steps(spec):
    with pytest.raises(ValueError):
        parse_sweep(spec)


def test_attach_options_name_the_drive_and_the_device_like_the_nodes():
    opts = attach_qemu_opts("pool1", "/s/server/probe-pool1.qcow2")
    assert opts == (
        "-drive werror=report,format=qcow2,id=probe-pool1,if=none,"
        "file=/s/server/probe-pool1.qcow2 "
        "-device virtio-blk-pci,drive=probe-pool1,serial=pool1,id=disk-pool1"
    )
    assert attach_commands("pool1", "/s/server/probe-pool1.qcow2") == [
        "drive_add 0 werror=report,format=qcow2,id=probe-pool1,if=none,"
        "file=/s/server/probe-pool1.qcow2",
        "device_add virtio-blk-pci,drive=probe-pool1,serial=pool1,id=disk-pool1",
    ]


@pytest.mark.parametrize("bad", ["/s/a b.qcow2", "/s/a,b.qcow2", "relative.qcow2"])
def test_attach_options_refuse_a_path_qemu_would_split(bad):
    with pytest.raises(ValueError):
        attach_qemu_opts("pool1", bad)


def test_monitor_reply_drops_the_echoed_command_and_the_prompt():
    echo = "d\x1b[K\x1b[Ddr\x1b[K\x1b[D\x1b[Ddrive_add 0 if=none\x1b[K"
    assert monitor_reply(echo + "\r\nOK\r\n(qemu) ") == "OK"
    assert monitor_reply(echo + "\r\n(qemu) ") == ""
    assert monitor_reply("Error: Duplicate ID\r\n(qemu) ") == "Error: Duplicate ID"
