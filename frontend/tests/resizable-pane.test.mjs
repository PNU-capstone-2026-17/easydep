import assert from 'node:assert/strict';
import test from 'node:test';

import {
  clampSidebarSize,
  DEFAULT_SIDEBAR_PERCENT,
  sidebarSizeBounds
} from '../src/lib/resizable-pane.ts';

test('resizable artifact panel keeps both panes usable', () => {
  const bounds = sidebarSizeBounds(1000);
  assert.equal(bounds.minimum, 30);
  assert.equal(bounds.maximum, 57.2);
  assert.equal(clampSidebarSize(10, 1000), bounds.minimum);
  assert.equal(clampSidebarSize(90, 1000), bounds.maximum);
  assert.equal(clampSidebarSize(DEFAULT_SIDEBAR_PERCENT, 1000), DEFAULT_SIDEBAR_PERCENT);
});

test('narrow layouts retain a movable range without overflowing both minimums', () => {
  const bounds = sidebarSizeBounds(600);
  assert.ok(bounds.minimum < bounds.maximum);
  assert.equal(clampSidebarSize(bounds.maximum + 10, 600), bounds.maximum);
});
