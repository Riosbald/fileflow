"""Copy text to the OS clipboard using whatever tool is already installed.

No dependency: tries ``pbcopy`` (macOS), ``clip`` (Windows), ``wl-copy`` (Wayland),
``xclip`` / ``xsel`` (X11). ``copy`` returns the tool's name, or ``None`` if the
clipboard is unavailable (headless box, SSH session) -- callers must have a
fallback instead of failing.
"""

import shutil
import subprocess

_CANDIDATES = [
    (["pbcopy"], "utf-8"),
    (["clip"], "utf-16-le"),
    (["wl-copy"], "utf-8"),
    (["xclip", "-selection", "clipboard"], "utf-8"),
    (["xsel", "--clipboard", "--input"], "utf-8"),
]


def copy(text):
    for argv, encoding in _CANDIDATES:
        exe = shutil.which(argv[0])
        if not exe:
            continue
        try:
            proc = subprocess.run([exe] + argv[1:], input=text.encode(encoding), timeout=10,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return argv[0]
    return None
