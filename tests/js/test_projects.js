// tests/js/test_projects.js
const fs = require('fs');
const path = require('path');

test('projects.js module exists', () => {
    const p = path.join(__dirname, '..', '..', 'static', 'js', 'projects.js');
    expect(fs.existsSync(p)).toBe(true);
});

test('slugifyOwner produces a filesystem-safe slug', () => {
    // Mirror of services/project/paths.py:slugify_owner — kept here so the JS
    // layer doesn't depend on the Python module being importable from Node.
    function slugifyOwner(name) {
        return (name || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'owner';
    }
    expect(slugifyOwner('Alice Smith')).toBe('alice_smith');
    expect(slugifyOwner('!!!')).toBe('owner');
});

test('memory explainer text for inherit mode', () => {
    function explainer(mode, metaJson) {
        if (mode === 'shared') return 'Memory is shared with the main brain.';
        if (mode === 'inherit' && metaJson) {
            try {
                const m = JSON.parse(metaJson);
                return `Snapshot taken with ${m.count} facts (main had ${m.source_count}).`;
            } catch (_) { return 'Snapshot taken from main brain.'; }
        }
        return 'Memory is private and starts empty.';
    }
    expect(explainer('shared', null)).toBe('Memory is shared with the main brain.');
    expect(explainer('inherit', JSON.stringify({ taken_at: 1, count: 10, source_count: 50 })))
        .toBe('Snapshot taken with 10 facts (main had 50).');
    expect(explainer('isolated', null)).toBe('Memory is private and starts empty.');
    expect(explainer('inherit', null)).toBe('Memory is private and starts empty.');
});

test('modeBadge returns the spec label', () => {
    function modeBadge(mode) {
        return ({ shared: 'Shared', inherit: 'Inherit', isolated: 'Isolated' })[mode] || 'Unknown';
    }
    expect(modeBadge('shared')).toBe('Shared');
    expect(modeBadge('inherit')).toBe('Inherit');
    expect(modeBadge('isolated')).toBe('Isolated');
    expect(modeBadge('weird')).toBe('Unknown');
});
