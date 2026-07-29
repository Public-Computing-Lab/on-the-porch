(function initializeChatMessageLifecycle(global) {
  'use strict';

  function randomHex(bytes) {
    const values = new Uint8Array(bytes);
    if (!global.crypto || typeof global.crypto.getRandomValues !== 'function') {
      throw new Error('Secure random number generation is unavailable.');
    }
    global.crypto.getRandomValues(values);
    return Array.from(values, (value) => value.toString(16).padStart(2, '0')).join('');
  }

  function createClientTurnId() {
    if (global.crypto && typeof global.crypto.randomUUID === 'function') {
      return global.crypto.randomUUID();
    }
    const hex = randomHex(16);
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
  }

  function payload(message, clientTurnId, extra = {}) {
    return { ...extra, message, client_turn_id: clientTurnId };
  }

  function isPending(result) {
    return Boolean(result && result.status === 202);
  }

  function authoritativeMessages(messages, {
    optimisticUserId,
    assistantPlaceholderId,
    userMessage,
    assistantMessage,
  }) {
    const filtered = (messages || []).filter((message) => (
      message
      && message.id !== optimisticUserId
      && message.id !== assistantPlaceholderId
      && message.id !== userMessage?.id
      && message.id !== assistantMessage?.id
    ));
    return [...filtered, userMessage, assistantMessage].filter(Boolean);
  }

  const api = {
    createClientTurnId,
    payload,
    isPending,
    authoritativeMessages,
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.ChatMessageLifecycle = api;
})(typeof window !== 'undefined' ? window : globalThis);
