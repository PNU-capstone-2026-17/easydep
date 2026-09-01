import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildSourceFileTree,
  visibleSourceTreeRows
} from '../src/lib/source-file-tree.ts';

const files = [
  { path: 'src/lib/api.ts' },
  { path: 'src/routes/+page.svelte' },
  { path: 'README.md' }
];

test('source explorer builds directories once and keeps depth information', () => {
  const rows = buildSourceFileTree(files);

  assert.deepEqual(
    rows.map((row) => [row.kind, row.path, row.depth]),
    [
      ['file', 'README.md', 0],
      ['directory', 'src', 0],
      ['directory', 'src/lib', 1],
      ['file', 'src/lib/api.ts', 2],
      ['directory', 'src/routes', 1],
      ['file', 'src/routes/+page.svelte', 2]
    ]
  );
});

test('collapsing a directory hides all descendants but keeps the directory row', () => {
  const rows = buildSourceFileTree(files);

  assert.deepEqual(
    visibleSourceTreeRows(rows, new Set(['src'])).map((row) => row.path),
    ['README.md', 'src']
  );
  assert.deepEqual(
    visibleSourceTreeRows(rows, new Set(['src/lib'])).map((row) => row.path),
    ['README.md', 'src', 'src/lib', 'src/routes', 'src/routes/+page.svelte']
  );
});
