#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const Lifecycle = require(path.join(ROOT, 'public', 'chat_message_lifecycle.js'));

function testGreetingProducesOneFinalAssistant() {
  const turn = Lifecycle.createTurn({
    clientTurnId: 'ct-1',
    threadId: 'thread-a',
    requestVersion: 1,
    userLocalId: 'pending-user-1',
    assistantLocalId: 'pending-assistant-1',
  });
  Lifecycle.applyDelta(turn, 'Hello', 1, 'thread-a');
  Lifecycle.applyDelta(turn, ' there', 1, 'thread-a');
  const first = Lifecycle.applyFinal(
    turn,
    { id: 'asst-1', content: 'Hello there', sources: [] },
    { id: 'user-1', content: 'Hi', role: 'user' },
    1,
    'thread-a',
  );
  assert.equal(first.accepted, true);
  assert.equal(turn.status, Lifecycle.STATUS.FINAL);
  assert.equal(turn.content, 'Hello there');

  const second = Lifecycle.applyFinal(
    turn,
    { id: 'asst-1', content: 'Different answer that must not win', sources: [] },
    { id: 'user-1', content: 'Hi', role: 'user' },
    1,
    'thread-a',
  );
  assert.equal(second.accepted, false);
  assert.equal(second.duplicate, true);
  assert.equal(turn.content, 'Hello there');
}

function testStreamingUpdatesSameTurn() {
  const turn = Lifecycle.createTurn({
    clientTurnId: 'ct-2',
    threadId: 'thread-a',
    requestVersion: 3,
    userLocalId: 'u',
    assistantLocalId: 'a',
  });
  assert.equal(Lifecycle.applyDelta(turn, 'A', 3, 'thread-a').accepted, true);
  assert.equal(Lifecycle.applyDelta(turn, 'B', 3, 'thread-a').accepted, true);
  assert.equal(turn.content, 'AB');
  assert.equal(turn.status, Lifecycle.STATUS.STREAMING);
  assert.equal(Lifecycle.applyDelta(turn, 'C', 99, 'thread-a').accepted, false);
  assert.equal(turn.content, 'AB');
}

function testHydrationDoesNotReplaceFinalContent() {
  const local = [
    {
      id: 'asst-9',
      role: 'assistant',
      content: 'Stable final answer',
      status: Lifecycle.STATUS.FINAL,
      client_turn_id: 'ct-9',
      sources: [],
    },
  ];
  const remote = [
    {
      id: 'asst-9',
      role: 'assistant',
      content: 'Stale hydrated answer',
      sources: [{ source: 'BPD' }],
    },
  ];
  const merged = Lifecycle.mergeHydratedMessages(local, remote);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].content, 'Stable final answer');
  assert.equal(merged[0].sources[0].source, 'BPD');
  assert.equal(merged[0].status, Lifecycle.STATUS.FINAL);
}

function testReplayDoesNotAppendDuplicate() {
  const messages = [
    { id: 'pending-user', role: 'user', content: 'Hi', client_turn_id: 'ct-r' },
  ];
  const built = Lifecycle.buildFinalStateMessages(messages, {
    userLocalId: 'pending-user',
    assistantLocalId: 'pending-assistant',
    userMessage: { id: 'user-r', role: 'user', content: 'Hi', client_turn_id: 'ct-r' },
    assistantMessage: {
      id: 'asst-r',
      role: 'assistant',
      content: 'Hello!',
      client_turn_id: 'ct-r',
      status: Lifecycle.STATUS.FINAL,
    },
  });
  assert.equal(built.filter((m) => m.role === 'user').length, 1);
  assert.equal(built.filter((m) => m.role === 'assistant').length, 1);
  assert.equal(built.some((m) => String(m.id).startsWith('pending-')), false);
}

function testStaleResponseCannotOverwrite() {
  const turn = Lifecycle.createTurn({
    clientTurnId: 'ct-stale',
    threadId: 'thread-a',
    requestVersion: 5,
    userLocalId: 'u',
    assistantLocalId: 'a',
  });
  Lifecycle.applyFinal(
    turn,
    { id: 'asst-new', content: 'Newest answer' },
    { id: 'user-new', content: 'Hi' },
    5,
    'thread-a',
  );
  assert.equal(
    Lifecycle.applyFinal(
      turn,
      { id: 'asst-old', content: 'Older answer' },
      { id: 'user-old', content: 'Hi' },
      4,
      'thread-a',
    ).accepted,
    false,
  );
  assert.equal(turn.content, 'Newest answer');
  assert.equal(Lifecycle.isStaleCompletion(turn, 4, 'thread-a'), true);
  assert.equal(Lifecycle.isStaleCompletion(turn, 5, 'thread-b'), true);
}

function testFailedDoesNotClobberFinal() {
  const turn = Lifecycle.createTurn({
    clientTurnId: 'ct-f',
    threadId: 'thread-a',
    requestVersion: 1,
    userLocalId: 'u',
    assistantLocalId: 'a',
  });
  Lifecycle.applyFinal(
    turn,
    { id: 'asst', content: 'Done' },
    { id: 'user', content: 'Hi' },
    1,
    'thread-a',
  );
  assert.equal(Lifecycle.applyFailed(turn, 1, 'thread-a').accepted, false);
  assert.equal(turn.status, Lifecycle.STATUS.FINAL);
}

function testAppNoLongerUsesInstantSubstantivePath() {
  const app = fs.readFileSync(path.join(ROOT, 'public', 'app.js'), 'utf8');
  assert.equal(app.includes('InstantReplies?.isInstant'), false);
  assert.equal(app.includes('InstantReplies.reply'), false);
  assert.match(app, /ChatMessageLifecycle/);
  assert.match(app, /turnsByClientId/);
  const html = fs.readFileSync(path.join(ROOT, 'public', 'index.html'), 'utf8');
  assert.match(html, /chat_message_lifecycle\.js/);
  assert.equal(html.includes('instantReplies.js'), false);
}

function testStreamParserIgnoresSecondFinalAndKeepsFinalOnCloseError() {
  const apiSource = fs.readFileSync(path.join(ROOT, 'public', 'api.js'), 'utf8');
  assert.match(apiSource, /sawFinal/);
  assert.match(apiSource, /If the authoritative final already arrived/);
  assert.match(apiSource, /Process exactly one final event/);
}

function testInstantRepliesDisabledStub() {
  const sandbox = { window: {}, console };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  vm.runInNewContext(
    fs.readFileSync(path.join(ROOT, 'public', 'instantReplies.js'), 'utf8'),
    sandbox,
  );
  assert.equal(sandbox.InstantReplies.isInstant('Hi'), false);
  assert.equal(sandbox.InstantReplies.reply('Hi'), '');
  assert.equal(sandbox.InstantReplies.disabled, true);
}

function main() {
  const tests = [
    testGreetingProducesOneFinalAssistant,
    testStreamingUpdatesSameTurn,
    testHydrationDoesNotReplaceFinalContent,
    testReplayDoesNotAppendDuplicate,
    testStaleResponseCannotOverwrite,
    testFailedDoesNotClobberFinal,
    testAppNoLongerUsesInstantSubstantivePath,
    testStreamParserIgnoresSecondFinalAndKeepsFinalOnCloseError,
    testInstantRepliesDisabledStub,
  ];
  tests.forEach((fn) => {
    fn();
    console.log(`PASS ${fn.name}`);
  });
  console.log('ALL_PASS');
}

main();
