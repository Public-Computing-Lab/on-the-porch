(function initUiEntityContext(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.UiEntityContext = api;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';

  const MAX_EVENT_ID = 2147483647;
  const MAX_TITLE_LENGTH = 255;

  function canonicalEventId(value) {
    if (typeof value === 'boolean' || value === null || value === undefined) return null;
    const text = String(value);
    if (!/^[1-9][0-9]*$/.test(text)) return null;
    const parsed = Number(text);
    if (!Number.isSafeInteger(parsed) || parsed > MAX_EVENT_ID) return null;
    return String(parsed);
  }

  function canonicalDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const [year, month, day] = value.split('-').map(Number);
    const parsed = new Date(Date.UTC(year, month - 1, day));
    if (
      parsed.getUTCFullYear() !== year
      || parsed.getUTCMonth() + 1 !== month
      || parsed.getUTCDate() !== day
    ) {
      return null;
    }
    return value;
  }

  function canonicalTitle(value) {
    if (typeof value !== 'string') return null;
    const title = value.trim();
    if (!title || title.length > MAX_TITLE_LENGTH || /[\u0000-\u001F\u007F]/.test(title)) {
      return null;
    }
    return title;
  }

  function freezeContext(context) {
    return Object.freeze({
      entity_type: 'event',
      entity_id: context.entity_id,
      entity_title: context.entity_title,
      entity_date: context.entity_date,
      source: 'events_sidebar',
    });
  }

  function buildEventUiContext(event) {
    if (!event || typeof event !== 'object' || Array.isArray(event)) return null;
    const entityId = canonicalEventId(event.id);
    const entityTitle = canonicalTitle(event.event_name);
    const entityDate = canonicalDate(event.start_date);
    if (!entityId || !entityTitle || !entityDate) return null;
    return freezeContext({
      entity_id: entityId,
      entity_title: entityTitle,
      entity_date: entityDate,
    });
  }

  function buildEventSelection(event, displayDate) {
    const context = buildEventUiContext(event);
    if (!context) return null;
    const safeDisplayDate = typeof displayDate === 'string' && displayDate.trim()
      ? displayDate.trim()
      : context.entity_date;
    return Object.freeze({
      message: `Tell me more about "${context.entity_title}" event happening on ${safeDisplayDate}.`,
      ui_context: context,
    });
  }

  function cloneContext(context) {
    return freezeContext(context);
  }

  function createPendingEventContextStore() {
    let pending = null;

    function clear() {
      pending = null;
    }

    function setSelection(selection) {
      if (!selection || typeof selection.message !== 'string' || !selection.ui_context) {
        clear();
        return false;
      }
      const context = buildEventUiContext({
        id: selection.ui_context.entity_id,
        event_name: selection.ui_context.entity_title,
        start_date: selection.ui_context.entity_date,
      });
      if (!context) {
        clear();
        return false;
      }
      pending = Object.freeze({ message: selection.message, ui_context: context });
      return true;
    }

    function setEvent(event, displayDate) {
      const selection = buildEventSelection(event, displayDate);
      if (!selection) {
        clear();
        return null;
      }
      setSelection(selection);
      return selection;
    }

    function createOutboundPayload(message) {
      const payload = { message: String(message || '') };
      if (pending && pending.message === payload.message) {
        payload.ui_context = cloneContext(pending.ui_context);
      }
      return Object.freeze(payload);
    }

    function restoreOutboundPayload(payload) {
      if (!payload || typeof payload.message !== 'string' || !payload.ui_context) {
        clear();
        return false;
      }
      return setSelection({
        message: payload.message,
        ui_context: payload.ui_context,
      });
    }

    function hasPendingFor(message) {
      return Boolean(pending && pending.message === message);
    }

    return Object.freeze({
      clear,
      setEvent,
      setSelection,
      createOutboundPayload,
      restoreOutboundPayload,
      hasPendingFor,
    });
  }

  return Object.freeze({
    buildEventUiContext,
    buildEventSelection,
    createPendingEventContextStore,
  });
}));
