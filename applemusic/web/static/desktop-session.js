// Loaded only by the desktop launcher; independent of Vue or remote resources.
(() => {
  let socket;
  let retry;
  let leaving = false;
  function connect() {
    if (leaving || (socket && socket.readyState <= WebSocket.OPEN)) return;
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${scheme}//${location.host}/api/desktop-session`);
    socket.onclose = () => {
      if (!leaving) retry = setTimeout(connect, 1000);
    };
  }
  window.addEventListener('pagehide', () => {
    leaving = true;
    clearTimeout(retry);
    if (socket) socket.close();
  });
  window.addEventListener('pageshow', () => {
    leaving = false;
    connect();
  });
  window.addEventListener('beforeunload', event => {
    if (window.__vueRoot && window.__vueRoot.isBusy) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  connect();
})();
