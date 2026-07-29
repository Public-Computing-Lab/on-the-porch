'use strict';

const assert = require('assert');

const lifecycle = require('../public/chat_message_lifecycle.js');

const first = lifecycle.createClientTurnId();
const second = lifecycle.createClientTurnId();
assert.match(first, /^[a-f0-9-]{36}$/i);
assert.notStrictEqual(first, second);

const payload = lifecycle.payload('Hello', first, { response_format: 'chat' });
assert.deepStrictEqual(payload, {
  response_format: 'chat',
  message: 'Hello',
  client_turn_id: first,
});
assert.strictEqual(lifecycle.isPending({ status: 202 }), true);
assert.strictEqual(lifecycle.isPending({ status: 201 }), false);

const optimisticUser = { id: `pending-user-${first}`, role: 'user' };
const placeholder = { id: `pending-assistant-${first}`, role: 'assistant' };
const user = { id: 'persisted-user', role: 'user' };
const assistant = { id: 'persisted-assistant', role: 'assistant' };
const result = lifecycle.authoritativeMessages(
  [optimisticUser, placeholder, user, assistant],
  {
    optimisticUserId: optimisticUser.id,
    assistantPlaceholderId: placeholder.id,
    userMessage: user,
    assistantMessage: assistant,
  },
);
assert.deepStrictEqual(result, [user, assistant]);

const samePayloadForSse = lifecycle.payload('Hello', first);
const samePayloadForJsonFallback = lifecycle.payload('Hello', first);
assert.strictEqual(
  samePayloadForSse.client_turn_id,
  samePayloadForJsonFallback.client_turn_id,
);

console.log('chat message lifecycle: 8 assertions passed');
