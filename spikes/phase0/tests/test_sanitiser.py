"""Sanitiser: host user names, host names and absolute host paths are removed;
guest paths survive."""

from spikectl.verify import HOST_PATH_PATTERNS
from testlib.sanitiser import Sanitiser

SANITISER = Sanitiser(
    user_names=("alice",),
    host_names=("alice-laptop", "buildbox"),
    host_roots=("/srv/work/nasaex", "/var/tmp/vmstate"),
)


def test_home_directory_paths_are_removed():
    text = "log at /home/alice/projects/nasaex/state/driver.log and /Users/bob/x"

    out = SANITISER.sanitise(text)

    assert "alice" not in out and "bob" not in out
    assert "/home/" not in out and "/Users/" not in out


def test_configured_host_roots_are_removed():
    out = SANITISER.sanitise("copy /srv/work/nasaex/spikes/phase0/state/s1/empty0.qcow2 done")

    assert "/srv/work" not in out
    assert out.endswith("done")


def test_user_and_host_names_are_removed_as_words():
    out = SANITISER.sanitise("alice@buildbox: started by alice on alice-laptop")

    assert "alice" not in out and "buildbox" not in out


def test_names_inside_longer_words_survive():
    assert SANITISER.sanitise("alicenet malice") == "alicenet malice"


def test_guest_paths_survive():
    text = "mounted /mnt/archive/disk0, parity /var/lib/snapraid/parity, store /nix/store/x-y"

    assert SANITISER.sanitise(text) == text


def test_sanitising_is_idempotent_and_leaves_nothing_the_verifier_would_flag():
    text = "/home/alice/a ~/b C:\\Users\\carol\\c /srv/work/nasaex/d and alice-laptop"

    once = SANITISER.sanitise(text)

    assert SANITISER.sanitise(once) == once
    assert not any(pattern.search(once) for pattern in HOST_PATH_PATTERNS)
