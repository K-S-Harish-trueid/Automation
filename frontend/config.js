// Where the backend API lives.
//
// - Leave as "/api" if this frontend is served BY the same FastAPI process
//   (the default local setup -- see backend/README.md).
// - If you host this frontend somewhere else (static hosting, a different
//   server, a different port), point this at the backend's full URL instead,
//   e.g. "http://192.168.1.20:8000/api" or "https://k2-backend.example.com/api".
window.K2_API_BASE = "/api";

// How often (ms) the frontend polls GET /jobs/{id}/progress while a job is
// mid-run. Lower = smoother progress bar, more requests. 900ms is a good
// default for a multi-second-to-multi-minute pipeline step.
window.K2_POLL_INTERVAL_MS = 900;
