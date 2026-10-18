"""The pool race probe (vm-tests/probes/probe_pool_race.py): parsing of the udev monitor log and
the btrfs scan lines it collects per boot, the per-boot classification of the not-ready pool
member, and the summary over the boots. The boots themselves need the VM."""

from probes.pool_race import (
    classify,
    member_events,
    parse_monitor,
    scan_lines,
    state_at,
    summarize_race,
)

# `udevadm monitor --kernel --udev --property --subsystem-match=block`, shortened: the header,
# then vdb (devid 1) not ready, vdc (devid 2) ready, the retrigger's change of vdb, vdb ready.
MONITOR = """\
monitor will print the received events for:
UDEV - the event which udev sends out after rule processing
KERNEL - the kernel uevent

KERNEL[5.270000] add      /devices/pci0000:00/0000:00:0a.0/virtio7/block/vdb (block)
ACTION=add
DEVPATH=/devices/pci0000:00/0000:00:0a.0/virtio7/block/vdb
SUBSYSTEM=block
DEVNAME=vdb
SEQNUM=2001
SYNTH_UUID=0

UDEV  [5.290000] add      /devices/pci0000:00/0000:00:0a.0/virtio7/block/vdb (block)
ACTION=add
DEVNAME=/dev/vdb
SEQNUM=2001
ID_FS_LABEL=spikepool
ID_BTRFS_READY=0
SYSTEMD_READY=0

UDEV  [5.295000] add      /devices/pci0000:00/0000:00:0b.0/virtio8/block/vdc (block)
ACTION=add
DEVNAME=/dev/vdc
SEQNUM=2002
ID_FS_LABEL=spikepool
ID_BTRFS_READY=1

KERNEL[5.300000] change   /devices/pci0000:00/0000:00:0a.0/virtio7/block/vdb (block)
ACTION=change
DEVNAME=vdb
SEQNUM=2010
SYNTH_UUID=5c1f

UDEV  [5.320000] change   /devices/pci0000:00/0000:00:0a.0/virtio7/block/vdb (block)
ACTION=change
DEVNAME=/dev/vdb
SEQNUM=2010
ID_FS_LABEL=spikepool
ID_BTRFS_READY=1
"""

SCANS = [
    (
        5.280569,
        "server kernel: BTRFS: device label spikepool devid 1 transid 9 /dev/vdb (253:16)"
        " scanned by (udev-worker) (363)",
    ),
    (
        5.282869,
        "server kernel: BTRFS: device label spikepool devid 2 transid 9 /dev/vdc (253:32)"
        " scanned by (udev-worker) (360)",
    ),
    (
        5.290000,
        "server kernel: BTRFS: device label arch0 devid 1 transid 9 /dev/vdd (253:48)"
        " scanned by (udev-worker) (361)",
    ),
    (6.000000, "server systemd[1]: Found device /dev/disk/by-label/spikepool."),
]


def test_parse_monitor_reads_header_time_action_and_properties():
    events = parse_monitor(MONITOR)
    assert [(e["source"], e["action"], e["sysname"]) for e in events] == [
        ("KERNEL", "add", "vdb"),
        ("UDEV", "add", "vdb"),
        ("UDEV", "add", "vdc"),
        ("KERNEL", "change", "vdb"),
        ("UDEV", "change", "vdb"),
    ]
    assert events[1]["t"] == 5.29
    assert events[1]["props"]["SYSTEMD_READY"] == "0"
    assert events[3]["props"]["SYNTH_UUID"] == "5c1f"


def test_parse_monitor_ignores_a_truncated_last_block_without_header():
    assert parse_monitor("ACTION=add\nDEVNAME=/dev/vdb\n") == []
    assert parse_monitor("") == []


def test_scan_lines_keep_the_pool_label_only():
    scans = scan_lines(SCANS, "spikepool")
    assert scans == {
        "vdb": {"t": 5.280569, "devid": 1, "worker_pid": 363},
        "vdc": {"t": 5.282869, "devid": 2, "worker_pid": 360},
    }


def test_member_events_and_state_at_a_time():
    events = parse_monitor(MONITOR)
    assert [(e["source"], e["action"]) for e in member_events(events, "vdb")] == [
        ("KERNEL", "add"),
        ("UDEV", "add"),
        ("KERNEL", "change"),
        ("UDEV", "change"),
    ]
    # Before any processed event, after the not-ready add, after the ready change.
    assert state_at(events, "vdb", 5.0) is None
    assert state_at(events, "vdb", 5.30) == {"ID_BTRFS_READY": "0", "SYSTEMD_READY": "0"}
    assert state_at(events, "vdb", 5.40) == {"ID_BTRFS_READY": "1", "SYSTEMD_READY": None}


def test_classify_a_rescued_boot():
    events = parse_monitor(MONITOR)
    boot = classify(events, ["vdb", "vdc"], scan_lines(SCANS, "spikepool"), "vdc", 5.296)
    assert boot["not_ready_member"] == "vdb"
    assert boot["not_ready_processed_s"] == 5.29
    assert boot["retrigger_changes"] == 1
    assert boot["not_ready_member_ready_s"] == 5.32
    assert boot["scan_gap_ms"] == 2.3
    assert boot["scan_order"] == ["vdb", "vdc"]
    assert boot["link_target"] == "vdc"
    assert boot["state_at_link"] == {
        "vdb": {"ID_BTRFS_READY": "0", "SYSTEMD_READY": "0"},
        "vdc": {"ID_BTRFS_READY": "1", "SYSTEMD_READY": None},
    }
    assert boot["stuck"] is False


def test_classify_a_stuck_member_that_owns_the_link():
    # The retrigger never reached vdb: no change event, vdb stays not ready and owns the link.
    events = [e for e in parse_monitor(MONITOR) if e["action"] != "change"]
    boot = classify(events, ["vdb", "vdc"], scan_lines(SCANS, "spikepool"), "vdb", 7.3)
    assert boot["retrigger_changes"] == 0
    assert boot["not_ready_member_ready_s"] is None
    assert boot["stuck"] is True
    assert boot["state_at_link"]["vdb"] == {"ID_BTRFS_READY": "0", "SYSTEMD_READY": "0"}


def test_classify_without_monitor_events_reports_nothing_found():
    boot = classify([], ["vdb", "vdc"], {}, None, None)
    assert boot["not_ready_member"] is None
    assert boot["scan_gap_ms"] is None
    assert boot["stuck"] is None


def test_summarize_race_counts_failures_rescues_and_gaps():
    boots = [
        {
            "index": 0,
            "up": True,
            "race": {
                "not_ready_member": "vdb",
                "retrigger_changes": 2,
                "stuck": False,
                "scan_gap_ms": 2.3,
            },
        },
        {
            "index": 1,
            "up": True,
            "race": {
                "not_ready_member": "vdc",
                "retrigger_changes": 1,
                "stuck": False,
                "scan_gap_ms": 0.1,
            },
        },
        {
            "index": 2,
            "up": False,
            "race": {
                "not_ready_member": "vdb",
                "retrigger_changes": 0,
                "stuck": True,
                "scan_gap_ms": 9.0,
            },
        },
    ]
    summary = summarize_race(boots)
    assert summary["boots"] == 3
    assert summary["failed_boots"] == [2]
    assert summary["stuck_boots"] == [2]
    assert summary["retrigger_changes"] == {"0": 1, "1": 1, "2": 1}
    assert summary["scan_gap_ms"] == {"n": 3, "min": 0.1, "median": 2.3, "max": 9.0}
    assert summary["zero_failure_bound"] is None  # a failure occurred


def test_summarize_race_bound_when_nothing_failed():
    boots = [
        {
            "index": i,
            "up": True,
            "race": {"retrigger_changes": 1, "stuck": False, "scan_gap_ms": 1.0},
        }
        for i in range(100)
    ]
    summary = summarize_race(boots)
    assert summary["failed_boots"] == []
    assert round(summary["zero_failure_bound"], 4) == 0.0295


def test_classify_counts_a_retrigger_sent_before_the_add_was_broadcast():
    # A trigger can find the database (written before RUN and the broadcast) while the add event
    # is still being finished: its change event is queued behind the add (higher SEQNUM) even
    # though it is logged before the add's udev broadcast. It still reaches the member.
    early = """\
KERNEL[5.285000] change   /devices/pci0000:00/0000:00:0a.0/virtio7/block/vdb (block)
ACTION=change
DEVNAME=vdb
SEQNUM=2005
SYNTH_UUID=77aa

"""
    events = parse_monitor(early + MONITOR)
    events.sort(key=lambda e: e["t"])
    boot = classify(events, ["vdb", "vdc"], scan_lines(SCANS, "spikepool"), "vdc", 5.296)
    assert boot["retrigger_changes"] == 2
    assert boot["retrigger_change_s"] == [5.285, 5.3]
