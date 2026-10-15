"""Remove host-specific data from evidence before it is packed or committed.

Host user names, host names and absolute host paths are replaced; guest paths such as
/mnt/archive/... survive. The path patterns are shared with `spikectl verify`, which detects
what the sanitiser is meant to remove."""

import re
from collections.abc import Iterable

_PATH_END = r"[^\s'\"`:,;)\]]"

# Home-directory style paths of any OS (constitution: no local absolute paths).
HOST_PATH_PATTERNS = (
    re.compile(rf"(?<![\w.])/home/{_PATH_END}+"),
    re.compile(rf"(?<![\w.])/Users/{_PATH_END}+"),
    re.compile(rf"[A-Za-z]:\\Users\\{_PATH_END}+"),
    re.compile(rf"(?<![\w/.~])~/{_PATH_END}*"),
    re.compile(rf"(?<![\w/.~])/tmp/{_PATH_END}+"),
)


def name_pattern(name: str) -> re.Pattern:
    """A user or host name as a whole token: not inside a longer word or a hyphenated name such
    as a package scope (`@alice-space/ui` is not the user `alice`)."""
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")


class Sanitiser:
    def __init__(
        self,
        user_names: Iterable[str] = (),
        host_names: Iterable[str] = (),
        host_roots: Iterable[str] = (),
    ):
        # Longest first so that "alice-laptop" is replaced before "alice".
        self._roots = [
            re.compile(re.escape(root.rstrip("/")) + rf"(?:/{_PATH_END}*)?")
            for root in sorted(host_roots, key=len, reverse=True)
        ]
        self._users = [name_pattern(n) for n in sorted(user_names, key=len, reverse=True)]
        self._hosts = [name_pattern(n) for n in sorted(host_names, key=len, reverse=True)]

    def sanitise(self, text: str) -> str:
        for pattern in (*self._roots, *HOST_PATH_PATTERNS):
            text = pattern.sub("<host-path>", text)
        for pattern in self._hosts:
            text = pattern.sub("<host>", text)
        for pattern in self._users:
            text = pattern.sub("<user>", text)
        return text
