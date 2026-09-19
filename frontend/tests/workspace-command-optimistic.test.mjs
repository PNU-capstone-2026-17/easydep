import assert from 'node:assert/strict';
import test from 'node:test';
import {
  optimisticCommandEvents,
  reconcileWorkspaceEvents,
  workspaceProgressCards
} from '../src/lib/workspace-timeline.ts';

const command = {
  command_id: 'command-1', app_id: 'app-1', action: 'message',
  stage: 'requirements', status: 'QUEUED', payload: {}
};

function event(eventId, metadata, commandId = 'command-1') {
  return {
    event_id: eventId, app_id: 'app-1', command_id: commandId, stage: 'requirements',
    kind: 'progress', actor: 'system', text: 'Progress updated', metadata,
    created_at: `2026-09-15T00:00:${String(eventId).padStart(2, '0')}Z`
  };
}

function cardsEvent(eventId, cards, progressEvent = 'durableProgressCards') {
  return event(eventId, { progress_event: progressEvent, progress_cards: { version: 1, cards } });
}

test('shows an optimistic user message and activity while the command is accepted', () => {
  const events = optimisticCommandEvents(command, 'Build a calculator', '2026-09-15T00:00:00Z');
  assert.equal(events.length, 2);
  assert.equal(events[0].actor, 'user');
  assert.equal(events[1].text, 'Starting requirements analysis...');
});

test('replaces optimistic events with durable events for the same command', () => {
  const optimistic = optimisticCommandEvents(command, 'Build a calculator');
  const durable = [
    { ...event(11, {}), kind: 'message', actor: 'user', text: 'Build a calculator' },
    event(12, { progress_event: 'commandStarting', status: 'RUNNING' })
  ];
  assert.deepEqual(reconcileWorkspaceEvents(optimistic, durable), durable);
});

test('keeps a submitted optimistic message if the refresh has not persisted it yet', () => {
  const optimistic = optimisticCommandEvents(command, 'Build a calculator');
  const refreshed = reconcileWorkspaceEvents(optimistic, []);
  assert.equal(refreshed.length, 2);
  assert.equal(refreshed.find((item) => item.actor === 'user')?.text, 'Build a calculator');
});

test('removes an unreplaced optimistic activity placeholder when its command is terminal', () => {
  const reconciled = reconcileWorkspaceEvents(optimisticCommandEvents(command, 'Build a calculator'), [], 'command-1');
  assert.equal(reconciled.length, 1);
  assert.equal(reconciled[0].actor, 'user');
});

test('projects cards separately by stable command and card ids', () => {
  const cards = workspaceProgressCards([
    cardsEvent(20, [{
      id: 'requirements', stage: 'requirements', order: 20, label: 'Requirements analysis', tasks: [
        { id: 'review', label: 'Review request', order: 2, status: 'PENDING' },
        { id: 'model', label: 'Model requirements', order: 1, status: 'RUNNING' }
      ]
    }]),
    { ...cardsEvent(21, [{
      id: 'requirements', label: 'Implementation work', order: 0,
      tasks: [{ id: 'write', label: 'Write sources', order: 0, status: 'PASS' }]
    }]), command_id: 'command-2', stage: 'implementation' }
  ]);
  assert.deepEqual(cards.map((card) => `${card.commandId}:${card.id}`), [
    'command-1:requirements', 'command-2:requirements'
  ]);
  assert.deepEqual(cards[0].tasks.map((task) => task.id), ['model', 'review']);
  assert.deepEqual(cards[0].tasks.map((task) => task.status), ['running', 'waiting']);
});

test('merges a refresh snapshot and a live patch without moving or duplicating its card', () => {
  const snapshot = cardsEvent(10, [{
    id: 'implementation', stage: 'implementation', order: 0, label: 'Implementation', event_id: 7,
    tasks: [{ id: 'backend', label: 'Backend', order: 0, status: 'PENDING' }]
  }]);
  const patch = cardsEvent(11, [{
    id: 'implementation', tasks: [{ id: 'backend', status: 'RUNNING', detail: 'Writing API' }]
  }], 'progressCardPatch');
  const cards = workspaceProgressCards(reconcileWorkspaceEvents([snapshot], [patch]));
  assert.equal(cards.length, 1);
  assert.equal(cards[0].eventId, 7);
  assert.deepEqual(cards[0].tasks, [{
    id: 'backend', label: 'Backend', order: 0, status: 'running', detail: 'Writing API'
  }]);
});

test('retains completed sibling tasks when a patch changes only one task', () => {
  const cards = workspaceProgressCards([
    cardsEvent(10, [{
      id: 'implementation', tasks: [
        { id: 'backend', label: 'Backend', order: 0, status: 'PASS' },
        { id: 'frontend', label: 'Frontend', order: 1, status: 'PENDING' }
      ]
    }]),
    event(11, {
      progress_event: 'progressCardPatch',
      progress_card: { id: 'implementation', tasks: [{ id: 'frontend', status: 'FAIL' }] }
    })
  ]);
  assert.deepEqual(cards[0].tasks.map((task) => [task.id, task.status]), [
    ['backend', 'completed'], ['frontend', 'failed']
  ]);
});

test('keeps explicit parent task relationships while normalizing task statuses', () => {
  const [card] = workspaceProgressCards([cardsEvent(10, [{
    id: 'requirements', tasks: [
      { id: 'specs', label: 'Write specifications', order: 0, status: 'RUNNING' },
      { id: 'UC1', label: 'Sign up', parent_id: 'specs', order: 1, status: 'PASS' },
      { id: 'UC2', label: 'Sign in', order: 2, status: 'PENDING' }
    ]
  }])]);
  assert.equal(card.tasks.find((task) => task.id === 'UC1')?.parentId, 'specs');
  assert.equal(card.tasks.find((task) => task.id === 'UC2')?.parentId, undefined);
  assert.deepEqual(card.tasks.map((task) => task.status), ['running', 'completed', 'waiting']);
});
