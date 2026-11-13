window.APP_CONFIG = window.APP_CONFIG || {};
// Point at the Flask API (api_v2.py / gunicorn on :8888). Required unless the
// frontend is served from port 8000 (see public/api.js auto-detect).
window.APP_CONFIG.apiBaseUrl = 'http://127.0.0.1:8888';
window.APP_CONFIG.streaming = true;
window.APP_CONFIG.chatTimeoutMs = 120000;
