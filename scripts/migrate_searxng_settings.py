#!/usr/bin/env python3
"""Make retained SearXNG settings inherit defaults without replacing them."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import yaml
from yaml.nodes import MappingNode
from yaml.tokens import BlockMappingStartToken, FlowMappingStartToken


_UTF8_BOM = b"\xef\xbb\xbf"


def _parse_root_mapping(text: str) -> tuple[MappingNode | None, dict]:
    """Parse settings with the same safe YAML semantics SearXNG uses."""
    try:
        loaded = yaml.safe_load(text)
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise ValueError("settings file is not valid single-document YAML") from None

    if loaded is None and node is None:
        return None, {}
    if not isinstance(loaded, dict) or not isinstance(node, MappingNode):
        raise ValueError("settings root is not a mapping")
    return node, loaded


def _flow_mapping_start(text: str) -> int:
    """Return the root flow mapping's opening-brace character offset."""
    try:
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            if isinstance(token, FlowMappingStartToken):
                return token.start_mark.index
    except yaml.YAMLError:
        pass
    raise ValueError("flow-style settings mapping has no opening brace")


def _newline_for(contents: bytes) -> bytes:
    first_lf = contents.find(b"\n")
    if first_lf > 0 and contents[first_lf - 1 : first_lf + 1] == b"\r\n":
        return b"\r\n"
    return b"\n"


def _block_mapping_position(text: str, root: MappingNode | None) -> tuple[int, int]:
    """Return a safe character offset and indent for a root block mapping key."""
    if root is None:
        return len(text), 0

    try:
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            if not isinstance(token, BlockMappingStartToken):
                continue
            line_start = token.start_mark.index - token.start_mark.column
            if not text[line_start : token.start_mark.index].strip():
                return line_start, token.start_mark.column
            return root.end_mark.index, token.start_mark.column
    except yaml.YAMLError:
        pass
    return root.end_mark.index, root.start_mark.column


def _add_block_default_inheritance(
    contents: bytes, text: str, root: MappingNode | None
) -> bytes:
    newline = _newline_for(contents)
    character_offset, indent_width = _block_mapping_position(text, root)
    bom_length = len(_UTF8_BOM) if contents.startswith(_UTF8_BOM) else 0
    offset = bom_length + len(text[:character_offset].encode("utf-8"))
    separator = b""
    if offset not in (0, bom_length) and not contents[:offset].endswith((b"\n", b"\r")):
        separator = newline
    addition = (
        separator
        + b" " * indent_width
        + b"use_default_settings: true"
        + newline
    )
    return contents[:offset] + addition + contents[offset:]


def migrate_settings(path: Path) -> bool:
    """Add the missing inheritance key atomically; return whether the file changed."""
    source_stat = path.lstat()
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError(f"settings path is not a regular file: {path}")

    contents = path.read_bytes()
    if not contents:
        return False

    text = contents.decode("utf-8-sig")
    root, loaded = _parse_root_mapping(text)
    if "use_default_settings" in loaded:
        return False

    if root is not None and root.flow_style:
        start = _flow_mapping_start(text)
        bom_length = len(_UTF8_BOM) if contents.startswith(_UTF8_BOM) else 0
        offset = bom_length + len(text[: start + 1].encode("utf-8"))
        separator = b", " if root.value else b""
        updated = (
            contents[:offset]
            + b"use_default_settings: true"
            + separator
            + contents[offset:]
        )
    else:
        updated = _add_block_default_inheritance(contents, text, root)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.odysseus-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        # chmod before chown: the Compose cap set is `cap_drop: ALL` plus
        # CHOWN/SETGID/SETUID/DAC_OVERRIDE, with no FOWNER. Once the temporary
        # file belongs to searxng:searxng — which every retained settings file
        # does, because searxng's entrypoint chowns /etc/searxng — root can no
        # longer chmod it and the migration dies with EPERM.
        os.fchmod(fd, stat.S_IMODE(source_stat.st_mode))
        os.fchown(fd, source_stat.st_uid, source_stat.st_gid)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)
    return True


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(f"usage: {Path(argv[0]).name} [settings.yml]", file=sys.stderr)
        return 2

    path = Path(argv[1]) if len(argv) == 2 else Path("/etc/searxng/settings.yml")
    try:
        changed = migrate_settings(path)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"SearXNG settings migration failed: {exc}", file=sys.stderr)
        return 1

    if changed:
        print("Added use_default_settings inheritance to retained SearXNG settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
