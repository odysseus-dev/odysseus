"""Unit tests for the Atlas wikilink/tag parser and graph builder.

Pure functions (no I/O), so these pin the link semantics directly: what counts
as a [[link]] / ![[embed]] / #tag, how links resolve to paths, and how the graph
is assembled (ghost nodes for unresolved links, no self-loops, deduped edges).
"""

from src.atlas_links import (
    parse_links,
    resolve_link,
    build_graph,
    backlinks,
    note_title,
)


def test_parse_links_wikilinks_aliases_headings():
    p = parse_links("See [[Note A]], [[Note B|alias]] and [[Note C#section]].")
    assert p["wikilinks"] == ["Note A", "Note B", "Note C"]


def test_parse_links_embeds_and_tags():
    p = parse_links("![[diagram.png]] tagged #project and #area/health")
    assert p["embeds"] == ["diagram.png"]
    assert p["tags"] == ["project", "area/health"]


def test_parse_links_ignores_code_and_numeric_tags():
    md = "real #topic\n`#notacode [[NotALink]]`\n```\n[[AlsoIgnored]] #nope\n```\n#123 not-a-tag"
    p = parse_links(md)
    assert p["wikilinks"] == []          # the only [[..]] were inside code
    assert "topic" in p["tags"]
    assert "123" not in p["tags"]        # purely-numeric is not a tag


def test_same_file_heading_link_is_not_an_outlink():
    assert parse_links("jump to [[#section]]")["wikilinks"] == []


def test_resolve_link_exact_and_basename():
    paths = ["a.md", "sub/b.md", "sub/deep/c.md"]
    assert resolve_link("a", paths) == "a.md"
    assert resolve_link("sub/b.md", paths) == "sub/b.md"
    assert resolve_link("c", paths) == "sub/deep/c.md"   # unique basename
    assert resolve_link("missing", paths) is None


def test_resolve_link_ambiguous_prefers_shortest():
    paths = ["b.md", "sub/b.md", "x/y/b.md"]
    assert resolve_link("b", paths) == "b.md"            # shallowest wins


def test_build_graph_ghost_node_and_no_self_link():
    g = build_graph({"a.md": "[[b]] [[ghost]] [[a]]", "b.md": "# B"})
    ids = {n["id"]: n for n in g["nodes"]}
    assert "a.md" in ids and "b.md" in ids
    ghost = [n for n in g["nodes"] if n["missing"]]
    assert len(ghost) == 1 and ghost[0]["title"] == "ghost"
    # a→a self-link is dropped; a→b and a→ghost remain.
    assert {"source": "a.md", "target": "b.md"} in g["links"]
    assert not any(l["source"] == l["target"] for l in g["links"])


def test_build_graph_dedupes_repeated_edges():
    g = build_graph({"a.md": "[[b]] and again [[b]]", "b.md": ""})
    assert len([l for l in g["links"] if l["source"] == "a.md"]) == 1


def test_backlinks():
    notes = {"a.md": "[[b]]", "b.md": "# B", "c.md": "[[b]] too"}
    assert sorted(backlinks("b.md", notes)) == ["a.md", "c.md"]
    assert backlinks("a.md", notes) == []


def test_note_title_prefers_h1_then_stem():
    assert note_title("x.md", "# Real Title\nbody") == "Real Title"
    assert note_title("sub/My Note.md", "no heading") == "My Note"
