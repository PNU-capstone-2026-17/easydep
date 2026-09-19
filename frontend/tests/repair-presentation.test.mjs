import assert from 'node:assert/strict';
import test from 'node:test';

import { isAutomaticRepairActive } from '../src/lib/repair-presentation.ts';

test('only an ACTIVE public repair state suppresses raw validation findings', () => {
  assert.equal(isAutomaticRepairActive(null), false);
  assert.equal(isAutomaticRepairActive({ result: { repair_state: { status: 'active' } } }), true);
  assert.equal(isAutomaticRepairActive({ result: { repair_state: { status: 'STALLED' } } }), false);
  assert.equal(isAutomaticRepairActive({ result: {} }), false);
});
