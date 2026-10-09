"""Scan a real directory into project metadata and a nested file tree.

Both views are built from :func:`fileflow.walker.walk_entries` -- the same
traversal the prompt engine uses -- so the tree, the file count and the prompt
cannot disagree about which files exist (nested ``.gitignore`` files, hidden
files, ignore globs and out-of-project symlinks are all decided in one place).
"""

import os

from ..walker import walk_entries


def project_info(root, include_hidden=False, ignore_gitignore=False, ignore_patterns=None, include_patterns=None):
    """Return ``{ root, name, fileCount, sizeBytes }`` for a project root."""
    name = os.path.basename(root) or root
    file_count = 0
    size_bytes = 0
    for entry in walk_entries([root], include_hidden, ignore_gitignore, ignore_patterns, include_patterns):
        if entry.is_dir or entry.reason is not None:
            continue
        file_count += 1
        try:
            size_bytes += os.path.getsize(entry.path)
        except OSError:
            pass
    return {"root": root, "name": name, "fileCount": file_count, "sizeBytes": size_bytes}


def build_tree(root, include_hidden=False, ignore_gitignore=False, ignore_patterns=None, include_patterns=None):
    """Return a nested tree of ``root`` as ``{ name, type, size, children? }``.

    Folders first, then files, each sorted. Only entries the prompt would also
    consider are listed.
    """
    top = []
    nodes = {os.path.normpath(root): top}  # directory path -> its children list
    for entry in walk_entries([root], include_hidden, ignore_gitignore, ignore_patterns, include_patterns):
        if entry.reason is not None:
            continue
        parent = nodes.get(os.path.normpath(os.path.dirname(entry.path)))
        if parent is None:
            continue
        name = os.path.basename(entry.path)
        if entry.is_dir:
            children = []
            nodes[os.path.normpath(entry.path)] = children
            parent.append({"name": name, "type": "dir", "children": children})
        else:
            try:
                size = os.path.getsize(entry.path)
            except OSError:
                size = 0
            parent.append({"name": name, "type": "file", "size": size})

    def order(children):
        children.sort(key=lambda n: (n["type"] != "dir", n["name"]))
        for n in children:
            if n["type"] == "dir":
                order(n["children"])

    def prune(children):
        """With an include list, a folder that holds no matching file is noise: drop it (recursively)."""
        kept = []
        for n in children:
            if n["type"] == "dir":
                n["children"] = prune(n["children"])
                if not n["children"]:
                    continue
            kept.append(n)
        return kept

    if include_patterns:
        top = prune(top)
    order(top)
    return top
