#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const UiEntityContext = require(path.join(ROOT, 'public', 'ui_entity_context.js'));

const EVENT = Object.freeze({
  id: 417,
  event_name: 'CREW & CADET STREET PARADE',
  start_date: '2026-07-13',
  description: 'Must never leave the browser.',
  location: 'Must never leave the browser.',
  url: 'https://client.invalid/event',
});

function testBoundedEventPayload() {
  const context = UiEntityContext.buildEventUiContext(EVENT);
  assert.deepEqual(context, {
    entity_type: 'event',
    entity_id: '417',
    entity_title: 'CREW & CADET STREET PARADE',
    entity_date: '2026-07-13',
    source: 'events_sidebar',
  });
  assert.equal(Object.isFrozen(context), true);
  assert.equal('description' in context, false);
  assert.equal('location' in context, false);
  assert.equal('url' in context, false);
}

function testCanonicalDateAndComposerText() {
  const selection = UiEntityContext.buildEventSelection(EVENT, 'Mon, Jul 13');
  assert.equal(
    selection.message,
    'Tell me more about "CREW & CADET STREET PARADE" event happening on Mon, Jul 13.',
  );
  assert.equal(selection.ui_context.entity_date, '2026-07-13');
}

function testInvalidEventProducesNoContext() {
  const invalid = [
    { ...EVENT, id: 0 },
    { ...EVENT, id: '1 OR 1=1' },
    { ...EVENT, event_name: 'x'.repeat(256) },
    { ...EVENT, event_name: 'Bad\nTitle' },
    { ...EVENT, start_date: '07/13/2026' },
    { ...EVENT, start_date: '2026-02-30' },
  ];
  invalid.forEach((event) => assert.equal(UiEntityContext.buildEventUiContext(event), null));
}

function testPendingContextLifecycle() {
  const store = UiEntityContext.createPendingEventContextStore();
  const selection = store.setEvent(EVENT, 'Mon, Jul 13');
  assert.ok(selection);
  assert.equal(store.hasPendingFor(selection.message), true);

  const first = store.createOutboundPayload(selection.message);
  const second = store.createOutboundPayload(selection.message);
  assert.notEqual(first, second);
  assert.notEqual(first.ui_context, second.ui_context);
  assert.equal(Object.isFrozen(first), true);
  assert.equal(Object.isFrozen(first.ui_context), true);

  // Manual edits, suggestions, thread changes, and successful sends all call clear().
  store.clear();
  assert.deepEqual(store.createOutboundPayload(selection.message), { message: selection.message });
  assert.equal(store.hasPendingFor(selection.message), false);
}

function testCapturedPayloadSurvivesFallbackAndRestoresExactly() {
  const store = UiEntityContext.createPendingEventContextStore();
  const selection = store.setEvent(EVENT, 'Mon, Jul 13');
  const captured = store.createOutboundPayload(selection.message);
  store.clear();

  // Streaming and JSON fallback receive this same immutable object.
  const streamPayload = captured;
  const fallbackPayload = captured;
  assert.equal(streamPayload, fallbackPayload);
  assert.equal(fallbackPayload.ui_context.entity_id, '417');

  assert.equal(store.restoreOutboundPayload(captured), true);
  const retry = store.createOutboundPayload(selection.message);
  assert.deepEqual(retry, captured);
  assert.equal(
    store.restoreOutboundPayload({ message: selection.message, ui_context: { bad: true } }),
    false,
  );
}

function testAppWiringUsesOnePayloadAndClearsManualActions() {
  const source = fs.readFileSync(path.join(ROOT, 'public', 'app.js'), 'utf8');
  assert.match(source, /function createClientTurnId\(/);
  assert.match(source, /async function sendJsonWithTurnRetry\(/);
  assert.match(
    source,
    /const outboundPayload = Object\.freeze\(\{\s*\.\.\.eventContextStore\.createOutboundPayload\(message\),\s*client_turn_id: clientTurnId,\s*\}\)/,
  );
  assert.match(source, /sendMessageStream\(threadId, outboundPayload/);
  assert.match(source, /sendJsonWithTurnRetry\(threadId, outboundPayload/);
  assert.match(source, /chatInput\.addEventListener\('input',[\s\S]*eventContextStore\.clear\(\)/);
  assert.match(source, /suggestions\.addEventListener\('click',[\s\S]*eventContextStore\.clear\(\)/);
  assert.match(source, /async function loadMessages\(threadId\) \{\s*eventContextStore\.clear\(\)/);
  assert.match(source, /function restoreFailedChatComposer[\s\S]*restoreOutboundPayload\(outboundPayload\)/);
  assert.match(source, /streamResult\.data\?\.code === 'conversation_conflict'/);
  assert.match(source, /idempotency_key_reused/);
  assert.match(source, /turn_in_progress/);
  assert.match(source, /keyReuseConflict/);
  assert.match(
    source,
    /if \(!conversationConflict && !keyReuseConflict\) \{[\s\S]*outboundPayload/,
  );
}

function testFrontendSseAbortReplayReconcilesOneBubble() {
  const source = fs.readFileSync(path.join(ROOT, 'public', 'app.js'), 'utf8');
  // Lost-final path retries JSON with the same outboundPayload/client_turn_id.
  assert.match(source, /committed SSE turn is replayed/);
  assert.match(
    source,
    /state\.messages = normalizeMessages\(\[\s*\.\.\.state\.messages\.filter\(\(item\) => item\.id !== optimisticMessageId\),\s*retryResult\.data\.user_message/,
  );
  assert.match(source, /retryResult\.data\.assistant_message/);
  assert.equal(source.includes('client_turn_id: crypto.randomUUID()'), false);
}

const tests = [
  testBoundedEventPayload,
  testCanonicalDateAndComposerText,
  testInvalidEventProducesNoContext,
  testPendingContextLifecycle,
  testCapturedPayloadSurvivesFallbackAndRestoresExactly,
  testAppWiringUsesOnePayloadAndClearsManualActions,
  testFrontendSseAbortReplayReconcilesOneBubble,
];

let failed = 0;
for (const test of tests) {
  try {
    test();
    console.log(`OK  ${test.name}`);
  } catch (error) {
    failed += 1;
    console.error(`FAIL ${test.name}: ${error.stack || error}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exitCode = failed ? 1 : 0;
