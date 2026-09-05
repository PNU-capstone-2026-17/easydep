import assert from 'node:assert/strict';
import test from 'node:test';

import {
  hasCompletedRevisionExecution,
  hasPendingRevisionPlan,
  revisionPlanTargetLabel
} from '../src/lib/types.ts';

test('revision plan is pending only on action-required events with plan metadata', () => {
  assert.equal(hasPendingRevisionPlan('action_required', { revision_plan: { status: 'pending' } }), true);
  assert.equal(hasPendingRevisionPlan('message', { revision_plan: { status: 'pending' } }), false);
  assert.equal(hasPendingRevisionPlan('action_required', {}), false);
});

test('revision plan labels prefer display labels and fall back to refs', () => {
  assert.equal(revisionPlanTargetLabel({ display_label: 'Login flow', ref: 'UC-1' }), 'Login flow');
  assert.equal(revisionPlanTargetLabel({ ref: 'UC-1' }), 'UC-1');
  assert.equal(revisionPlanTargetLabel({ display_label: '  ', ref: 'UC-1' }), 'UC-1');
});

test('revision execution is shown only on completed status events', () => {
  const execution = { changed_stages: ['class_diagram'] };
  assert.equal(hasCompletedRevisionExecution('status', { status: 'COMPLETED', revision_execution: execution }), true);
  assert.equal(hasCompletedRevisionExecution('action_required', { status: 'COMPLETED', revision_execution: execution }), false);
  assert.equal(hasCompletedRevisionExecution('status', { status: 'RUNNING', revision_execution: execution }), false);
  assert.equal(hasCompletedRevisionExecution('status', { status: 'COMPLETED' }), false);
});
