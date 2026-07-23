/**
 * Local optimistic greetings are disabled.
 *
 * Showing InstantReplies then replacing them with the authoritative server answer
 * caused a visible flicker (one substantive assistant message replaced by another).
 * Resident answers now come only from the server JSON/SSE finalization path.
 *
 * Kept as a no-op stub so any stale cache that still loads this file does not break.
 */
(function initInstantRepliesDisabled(global) {
  function isInstant() {
    return false;
  }

  function reply() {
    return '';
  }

  function normalize(message) {
    return String(message || '')
      .trim()
      .toLowerCase()
      .replace(/[^\w\s']/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  global.InstantReplies = { isInstant, reply, normalize, disabled: true };
})(typeof window !== 'undefined' ? window : globalThis);
