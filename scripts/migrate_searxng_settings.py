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


def _formats_list(loaded: dict) -> list | None:
    """Return the explicit search.formats list, or None if absent."""
    search = loaded.get("search")
    if not isinstance(search, dict):
        return None
    formats = search.get("formats")
    if not isinstance(formats, list):
        return None
    return formats


def _insert_formats_into_flow_mapping(
    contents: bytes, text: str, value_node: MappingNode, bom_length: int
) -> bytes:
    """Insert ``formats: [html, json]`` into a flow-style mapping before its closing brace.

    The value_node positions are in ``text`` (not counting the BOM bytes).
    Returns the updated contents, or the original if the brace cannot be found.
    """
    start = value_node.start_mark.index
    end = value_node.end_mark.index
    flow_text = text[start:end]

    last_brace = flow_text.rfind("}")
    if last_brace == -1:
        return contents  # malformed; leave unchanged

    inner = flow_text[1:last_brace]
    separator = ", " if inner.strip() else ""
    new_flow = (
        flow_text[:last_brace] + separator + "formats: [html, json]" + flow_text[last_brace:]
    )

    byte_start = bom_length + len(text[:start].encode("utf-8"))
    byte_end = bom_length + len(text[:end].encode("utf-8"))
    return contents[:byte_start] + new_flow.encode("utf-8") + contents[byte_end:]


def _insert_search_formats(
    contents: bytes, text: str, root: MappingNode, loaded: dict, indent_width: int
) -> bytes:
    """Insert search.formats into the block mapping, preserving all other content.

    If ``search`` already exists as a block mapping key, the formats list is
    inserted at the end of that sub-mapping. If ``search`` is a flow-style
    mapping, formats are inserted into the flow value in-place to avoid
    creating a duplicate root key. If ``search`` does not exist, the entire
    ``search.formats`` block is appended at the end of the document (before
    any trailing YAML document-end marker ``...``).

    Child indentation is derived from the existing ``search`` block's first key
    rather than being hard-coded at two spaces.
    """
    newline = _newline_for(contents)
    indent = b" " * indent_width
    bom_length = len(_UTF8_BOM) if contents.startswith(_UTF8_BOM) else 0

    # Walk the root mapping node to find the ``search`` key's value node.
    for key_node, value_node in root.value:
        if key_node.value == "search":
            if value_node.id == "mapping" and not value_node.flow_style:
                # Derive child indentation from the first existing key in the
                # search block so non-two-space files stay consistent.
                if value_node.value:
                    child_indent = value_node.value[0][0].start_mark.column - indent_width
                    child_indent = max(child_indent, 1)
                else:
                    child_indent = 2
                inner_indent = b" " * (indent_width + child_indent)
                seq_indent = b" " * (indent_width + child_indent * 2)

                # Insert formats at the end of the existing search block.
                insert_offset = (
                    bom_length
                    + len(text[: value_node.end_mark.index].encode("utf-8"))
                )
                tail = contents[insert_offset:]
                if not contents[:insert_offset].endswith((b"\n", b"\r")):
                    prefix = newline
                else:
                    prefix = b""
                addition = (
                    prefix
                    + inner_indent + b"formats:" + newline
                    + seq_indent + b"- html" + newline
                    + seq_indent + b"- json" + newline
                )
                return contents[:insert_offset] + addition + tail
            elif value_node.id == "mapping" and value_node.flow_style:
                # search exists as a flow-style mapping. Insert formats into
                # the flow value rather than appending a duplicate root key
                # that would shadow the original under standard YAML loaders.
                return _insert_formats_into_flow_mapping(
                    contents, text, value_node, bom_length
                )
            # search exists but is not a mapping (e.g. scalar or sequence).
            # Leave the file unchanged for this key.
            break

    # No usable search block found -- append one.
    # Use two-space child indentation (conventional default for new blocks).
    block = (
        indent + b"search:" + newline
        + indent + b"  formats:" + newline
        + indent + b"    - html" + newline
        + indent + b"    - json" + newline
    )

    # Respect any trailing YAML document-end marker ``...`` to avoid producing
    # a second document that standard loaders silently discard or reject.
    stripped = contents.rstrip(b"\r\n ")
    if stripped.endswith(b"..."):
        # Locate the start of the trailing ``...`` line.
        dot_pos = len(stripped) - 3
        line_start = contents.rfind(b"\n", 0, dot_pos)
        if line_start == -1:
            line_start = 0
        else:
            line_start += 1  # step past the newline
        separator = b"" if contents[:line_start].endswith((b"\n", b"\r")) else newline
        return contents[:line_start] + separator + block + contents[line_start:]

    if not contents.endswith((b"\n", b"\r")):
        block = newline + block
    return contents + block


def migrate_settings(path: Path) -> bool:
    """Add the missing inheritance key and search formats atomically.

    Returns True if the file was changed, False if it was already fully
    migrated (both ``use_default_settings`` and ``search.formats`` present).
    """
    source_stat = path.lstat()
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError(f"settings path is not a regular file: {path}")

    contents = path.read_bytes()
    if not contents:
        return False

    text = contents.decode("utf-8-sig")
    root, loaded = _parse_root_mapping(text)

    needs_inheritance = "use_default_settings" not in loaded
    needs_formats = _formats_list(loaded) is None

    if not needs_inheritance and not needs_formats:
        return False

    updated = contents

    if needs_inheritance:
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

    if needs_formats:
        bom_length = len(_UTF8_BOM) if updated.startswith(_UTF8_BOM) else 0
        updated_text = updated[bom_length:].decode("utf-8-sig")
        updated_root, _ = _parse_root_mapping(updated_text)

        if updated_root is not None and updated_root.flow_style:
            # For flow-style roots, insert formats into the flow ``search``
            # value if it exists as a flow mapping.
            inserted = False
            for key_node, value_node in updated_root.value:
                if (
                    key_node.value == "search"
                    and value_node.id == "mapping"
                    and value_node.flow_style
                ):
                    updated = _insert_formats_into_flow_mapping(
                        updated, updated_text, value_node, bom_length
                    )
                    inserted = True
                    break
            if not inserted:
                # Flow-style roots without a block-accessible search section
                # cannot be safely extended by text substitution. Warn so the
                # operator can add the key manually.
                print(
                    "Warning: search.formats was not added because this settings"
                    " file uses a flow-style root mapping with no flow-style search"
                    " section. Add 'search: {formats: [html, json]}' manually.",
                    file=sys.stderr,
                )
        elif updated_root is not None:
            _, indent_width = _block_mapping_position(text, root)
            updated = _insert_search_formats(
                updated, updated_text, updated_root, loaded, indent_width
            )

    if updated == contents:
        return False

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.odysseus-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        # chmod before chown: the Compose cap set is `cap_drop: ALL` plus
        # CHOWN/SETGID/SETUID/DAC_OVERRIDE, with no FOWNER. Once the temporary
        # file belongs to searxng:searxng -- which every retained settings file
        # does, because searxng's entrypoint chowns /etc/searxng -- root can no
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
        print(
            "Migrated retained SearXNG settings: added use_default_settings"
            " inheritance and ensured search.formats includes json"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
