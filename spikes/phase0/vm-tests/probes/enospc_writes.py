"""Guest code and classification of the ENOSPC probes (tasks T051b and T051c).

SOURCE_SCRIPT writes the seeded source file (testlib.fixtures content, so the client and the
server generate the same bytes and every prefix is checkable). WRITER copies the source to a
target in 1 MiB writes, then fsync and close, unbuffered, so that the bytes it reports are the
bytes the write calls accepted, and it names the step that failed. `leftover` classifies what a
failed write left at the target path. Standard library and testlib only; the probes import it
as the script package `probes`, and so does check S1.9 (vm-tests/s1/checks_large_space.py,
task T047a), so a change here changes that check. Scaffolding for the spikes, not product
code."""

SOURCE_SCRIPT = """import sys
from testlib.fixtures import Entry, content_chunks
entry = Entry("enospc-source.bin", "pool", size=int(sys.argv[2]))
with open(sys.argv[1], "wb") as handle:
    for chunk in content_chunks("enospc-probe", entry):
        handle.write(chunk)"""

WRITER = """import errno, json, os, sys
path, mode, source, size = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
written, step, failed, handle = 0, "open", None, None
try:
    handle = open(path, mode, buffering=0)
    step = "write"
    with open(source, "rb") as reader:
        while written < size:
            block = reader.read(min(1 << 20, size - written))
            view = memoryview(block)
            while view:
                count = handle.write(view)
                written += count
                view = view[count:]
    step = "fsync"
    os.fsync(handle.fileno())
    step = "close"
    handle.close()
    step = None
except OSError as error:
    failed = errno.errorcode.get(error.errno, str(error.errno))
    if handle is not None and not handle.closed:
        try:
            handle.close()
        except OSError:
            pass
print(json.dumps({"errno": failed, "step": step if failed else None, "written": written}))"""


def leftover(found: dict | None, prefix_sha256: str | None, size: int) -> str:
    """What a write of `size` source bytes left at its path: `found` is the file's size and
    SHA-256 (None: no file), `prefix_sha256` the SHA-256 of that many source bytes."""
    if found is None:
        return "absent"
    if found["size"] == 0:
        return "empty"
    intact = found["sha256"] == prefix_sha256
    if found["size"] >= size:
        return "complete" if intact else "full size, content differs"
    return "partial, prefix intact" if intact else "partial, prefix differs"
