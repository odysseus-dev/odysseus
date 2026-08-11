// Pure-logic tests for static/js/memoryGraph.js: category->color resolution,
// API-response-to-Cytoscape-elements mapping, the fetch query string, the
// bundled demo graph's shape, and the isolate-component BFS. DOM/Cytoscape
// rendering behavior is covered by manual browser verification instead (see
// docs/progress.md) — there is no automated DOM test harness in this repo.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadMemoryGraph } from './graphHarness.mjs';

const mg = loadMemoryGraph();

test('_nodeSize scales with uses and clamps to [20, 60]', () => {
  assert.equal(mg.__nodeSize({ uses: 0 }), 20);
  assert.equal(mg.__nodeSize({}), 20);
  assert.equal(mg.__nodeSize({ uses: 3 }), 32);
  assert.equal(mg.__nodeSize({ uses: 100 }), 60);
});

test('_categoryColor resolves each known category to a distinct color', () => {
  const colors = mg.__KNOWN_CATEGORIES.map((c) => mg.__categoryColor(c));
  assert.equal(new Set(colors).size, colors.length, 'expected every known category to map to a distinct color');
});

test('_categoryColor falls back for an unrecognized category', () => {
  assert.equal(mg.__categoryColor('totally-unknown-category'), '#fallback-color');
});

test('_buildQuery always over-fetches below the UI slider floor', () => {
  const params = new URLSearchParams(mg.__buildQuery());
  assert.equal(params.get('min_similarity'), '0.5');
  assert.equal(params.get('max_edges_per_node'), '8');
});

test('_toElements maps every node and truncates long labels with an ellipsis', () => {
  const graph = {
    nodes: [
      { id: 'a', text: 'short', category: 'fact', uses: 1 },
      { id: 'b', text: 'x'.repeat(60), category: 'fact', uses: 1 },
    ],
    edges: [],
  };
  const { nodes } = mg.__toElements(graph);
  assert.equal(nodes.length, 2);
  assert.equal(nodes[0].data.label, 'short');
  assert.equal(nodes[1].data.label.endsWith('…'), true);
  assert.equal(nodes[1].data.label.length, 43); // 42 chars + ellipsis
});

test('_toElements drops edges whose endpoints are not among the given nodes', () => {
  // This is exactly the referential-integrity class of bug the demo-graph
  // test below also guards: an edge naming a node id that doesn't exist
  // must never reach Cytoscape (it throws on element construction otherwise).
  const graph = {
    nodes: [{ id: 'a', text: 'A', category: 'fact' }],
    edges: [
      { source: 'a', target: 'missing', type: 'similarity', weight: 0.9 },
      { source: 'missing', target: 'a', type: 'similarity', weight: 0.9 },
    ],
  };
  const { edges } = mg.__toElements(graph);
  assert.equal(edges.length, 0);
});

test('DEMO_GRAPH nodes and edges are internally consistent', () => {
  const graph = mg.__DEMO_GRAPH;
  const ids = new Set(graph.nodes.map((n) => n.id));
  assert.equal(graph.meta.node_count, graph.nodes.length);
  assert.equal(graph.meta.edge_count, graph.edges.length);
  for (const edge of graph.edges) {
    assert.ok(ids.has(edge.source), `edge source "${edge.source}" is not a real demo node`);
    assert.ok(ids.has(edge.target), `edge target "${edge.target}" is not a real demo node`);
  }
  for (const node of graph.nodes) {
    assert.ok(mg.__KNOWN_CATEGORIES.includes(node.category), `demo node has unknown category "${node.category}"`);
  }
});

test('_componentNodeIds returns the full connected component, undirected', () => {
  const graph = {
    nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }],
    edges: [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      // 'd' is disconnected from a/b/c
    ],
  };
  const component = mg.__componentNodeIds(graph, 'a');
  assert.deepEqual([...component].sort(), ['a', 'b', 'c']);
});

test('_componentNodeIds isolates a node with no edges to just itself', () => {
  const graph = { nodes: [{ id: 'a' }, { id: 'b' }], edges: [] };
  const component = mg.__componentNodeIds(graph, 'a');
  assert.deepEqual([...component], ['a']);
});

test('_componentNodeIds returns an empty set for an id not in the graph', () => {
  const graph = { nodes: [{ id: 'a' }], edges: [] };
  const component = mg.__componentNodeIds(graph, 'does-not-exist');
  assert.equal(component.size, 0);
});
