/**
 * Stable chat-turn lifecycle helpers for On the Porch.
 * Keeps one assistant message per client_turn_id through pending → streaming → final.
 *
 * Works in browsers (window.ChatMessageLifecycle) and Node tests (module.exports).
 */
(function initChatMessageLifecycle(global) {
  'use strict';

  const STATUS = Object.freeze({
    PENDING: 'pending',
    STREAMING: 'streaming',
    FINAL: 'final',
    FAILED: 'failed',
  });

  function createTurn({
    clientTurnId,
    threadId,
    requestVersion,
    userLocalId,
    assistantLocalId,
  }) {
    return {
      clientTurnId: String(clientTurnId || ''),
      threadId: threadId || null,
      requestVersion: Number(requestVersion) || 0,
      sequence: 0,
      status: STATUS.PENDING,
      userLocalId: userLocalId || null,
      assistantLocalId: assistantLocalId || null,
      serverUserId: null,
      serverAssistantId: null,
      content: '',
      sources: [],
      finalizedSequence: null,
    };
  }

  function nextSequence(turn) {
    turn.sequence += 1;
    return turn.sequence;
  }

  function canAccept(turn, requestVersion, activeThreadId) {
    if (!turn) return false;
    if (turn.status === STATUS.FINAL) return false;
    if (Number(requestVersion) !== Number(turn.requestVersion)) return false;
    if (activeThreadId && turn.threadId && activeThreadId !== turn.threadId) return false;
    return true;
  }

  function applyDelta(turn, text, requestVersion, activeThreadId) {
    if (!canAccept(turn, requestVersion, activeThreadId)) {
      return { accepted: false, turn, sequence: turn ? turn.sequence : 0 };
    }
    if (!text) {
      return { accepted: false, turn, sequence: turn.sequence };
    }
    turn.status = STATUS.STREAMING;
    turn.content = `${turn.content || ''}${text}`;
    const sequence = nextSequence(turn);
    return { accepted: true, turn, sequence };
  }

  function applyCorrection(turn, text, requestVersion, activeThreadId) {
    if (!canAccept(turn, requestVersion, activeThreadId)) {
      return { accepted: false, turn, sequence: turn ? turn.sequence : 0 };
    }
    turn.status = STATUS.STREAMING;
    turn.content = String(text || '');
    const sequence = nextSequence(turn);
    return { accepted: true, turn, sequence };
  }

  function applyFinal(turn, assistantMessage, userMessage, requestVersion, activeThreadId) {
    if (!turn) {
      return { accepted: false, reason: 'missing_turn' };
    }
    if (Number(requestVersion) !== Number(turn.requestVersion)) {
      return { accepted: false, reason: 'stale_version', turn };
    }
    if (activeThreadId && turn.threadId && activeThreadId !== turn.threadId) {
      return { accepted: false, reason: 'wrong_thread', turn };
    }
    if (turn.status === STATUS.FINAL) {
      // Idempotent: same logical final is ignored.
      return { accepted: false, reason: 'already_final', turn, duplicate: true };
    }
    const content = assistantMessage && typeof assistantMessage.content === 'string'
      ? assistantMessage.content
      : '';
    turn.status = STATUS.FINAL;
    turn.content = content;
    turn.sources = Array.isArray(assistantMessage?.sources) ? assistantMessage.sources : [];
    turn.serverAssistantId = assistantMessage?.id || turn.serverAssistantId;
    turn.serverUserId = userMessage?.id || turn.serverUserId;
    turn.finalizedSequence = nextSequence(turn);
    return {
      accepted: true,
      turn,
      sequence: turn.finalizedSequence,
      userMessage: userMessage || null,
      assistantMessage: {
        ...(assistantMessage || {}),
        id: turn.serverAssistantId || turn.assistantLocalId,
        role: 'assistant',
        content: turn.content,
        sources: turn.sources,
        client_turn_id: turn.clientTurnId,
        status: STATUS.FINAL,
      },
    };
  }

  function applyFailed(turn, requestVersion, activeThreadId) {
    if (!turn) return { accepted: false };
    if (turn.status === STATUS.FINAL) {
      return { accepted: false, reason: 'already_final', turn };
    }
    if (Number(requestVersion) !== Number(turn.requestVersion)) {
      return { accepted: false, reason: 'stale_version', turn };
    }
    if (activeThreadId && turn.threadId && activeThreadId !== turn.threadId) {
      return { accepted: false, reason: 'wrong_thread', turn };
    }
    turn.status = STATUS.FAILED;
    nextSequence(turn);
    return { accepted: true, turn };
  }

  function isStaleCompletion(turn, requestVersion, activeThreadId) {
    if (!turn) return true;
    if (Number(requestVersion) !== Number(turn.requestVersion)) return true;
    if (activeThreadId && turn.threadId && activeThreadId !== turn.threadId) return true;
    return false;
  }

  /**
   * Merge server history into local messages without regressing finalized content.
   * Match by server message id first, then by client_turn_id + role.
   */
  function mergeHydratedMessages(localMessages, remoteMessages) {
    const local = Array.isArray(localMessages) ? localMessages.slice() : [];
    const remote = Array.isArray(remoteMessages) ? remoteMessages.slice() : [];
    const byId = new Map();
    const byTurnRole = new Map();

    local.forEach((message, index) => {
      if (!message || typeof message !== 'object') return;
      if (message.id) byId.set(String(message.id), { message, index });
      if (message.client_turn_id && message.role) {
        byTurnRole.set(`${message.client_turn_id}:${message.role}`, { message, index });
      }
    });

    const result = local.map((message) => ({ ...message }));

    remote.forEach((remoteMessage) => {
      if (!remoteMessage || typeof remoteMessage !== 'object') return;
      const remoteId = remoteMessage.id ? String(remoteMessage.id) : null;
      let targetIndex = -1;

      if (remoteId && byId.has(remoteId)) {
        targetIndex = byId.get(remoteId).index;
      } else if (remoteMessage.client_turn_id && remoteMessage.role) {
        const key = `${remoteMessage.client_turn_id}:${remoteMessage.role}`;
        if (byTurnRole.has(key)) {
          targetIndex = byTurnRole.get(key).index;
        }
      }

      if (targetIndex >= 0) {
        const existing = result[targetIndex];
        const existingFinal = existing.status === STATUS.FINAL
          || (existing.id && !String(existing.id).startsWith('pending-'));
        if (existingFinal && typeof existing.content === 'string' && existing.content.length > 0) {
          // Preserve finalized answer text; allow metadata/source reconciliation.
          result[targetIndex] = {
            ...existing,
            ...remoteMessage,
            content: existing.content,
            status: STATUS.FINAL,
            client_turn_id: existing.client_turn_id || remoteMessage.client_turn_id,
            sources: (Array.isArray(remoteMessage.sources) && remoteMessage.sources.length)
              ? remoteMessage.sources
              : existing.sources,
          };
        } else {
          result[targetIndex] = {
            ...existing,
            ...remoteMessage,
            client_turn_id: existing.client_turn_id || remoteMessage.client_turn_id,
            status: remoteMessage.status || existing.status || STATUS.FINAL,
          };
        }
        return;
      }

      result.push({
        ...remoteMessage,
        status: remoteMessage.status || STATUS.FINAL,
      });
    });

    return result;
  }

  function buildFinalStateMessages(messages, {
    userLocalId,
    assistantLocalId,
    userMessage,
    assistantMessage,
  }) {
    const filtered = (Array.isArray(messages) ? messages : []).filter((entry) => {
      if (!entry) return false;
      if (userLocalId && entry.id === userLocalId) return false;
      if (assistantLocalId && entry.id === assistantLocalId) return false;
      return true;
    });
    return [
      ...filtered,
      userMessage,
      assistantMessage,
    ].filter(Boolean);
  }

  const api = {
    STATUS,
    createTurn,
    applyDelta,
    applyCorrection,
    applyFinal,
    applyFailed,
    isStaleCompletion,
    mergeHydratedMessages,
    buildFinalStateMessages,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  global.ChatMessageLifecycle = api;
})(typeof window !== 'undefined' ? window : globalThis);
