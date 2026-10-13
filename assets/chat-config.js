// Public chat address only. Model credentials stay on the private backend.
(() => {
  if (!window.XiongDayeChat?.endpoint) {
    const localPreview = ["http://127.0.0.1:8786", "http://localhost:8786"].includes(window.location?.origin);
    const endpoint = localPreview
      ? "http://127.0.0.1:8788/api/chat"
      : "https://relocation-exception-cultures-reform.trycloudflare.com/api/chat";
    window.XiongDayeChat = Object.freeze({endpoint});
  }
})();
