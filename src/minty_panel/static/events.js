// Reconnecting /api/events socket shared by all pages.
// At most one socket exists at a time; stop() cancels any pending reconnect.
// Close code 4401 means the server rejected the session: that is signalled once as
// "minty:unauthorized" and never retried (retrying would loop forever on a dead session).
// The server accepts the handshake before closing with 4401, so browsers see the real code and no
// separate status probe is needed.
const UNAUTHORIZED_CLOSE = 4401;

export function makeEvents({ onError = () => {} } = {}) {
  const handlers = new Set();
  let socket = null;
  let timer = null;
  let stopped = true;

  function detach() {
    clearTimeout(timer);
    timer = null;
    if (socket) {
      const old = socket;
      socket = null;
      old.onclose = old.onmessage = null;
      old.close();
    }
  }

  function connect() {
    timer = null;
    if (stopped || socket) return;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const current = new WebSocket(`${scheme}://${location.host}/api/events`);
    socket = current;
    current.onmessage = (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch { return; }
      for (const fn of [...handlers]) {
        try { fn(event); } catch (e) { onError(e); }
      }
    };
    current.onclose = (event) => {
      if (socket !== current) return;
      socket = null;
      if (stopped) return;
      if (event?.code === UNAUTHORIZED_CLOSE) {
        stopped = true;
        if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("minty:unauthorized"));
        return;
      }
      timer = setTimeout(connect, 3000);
    };
  }

  return {
    on(fn) { handlers.add(fn); return () => handlers.delete(fn); },
    start() { if (!stopped) return; stopped = false; connect(); },
    stop() { stopped = true; detach(); },
  };
}
