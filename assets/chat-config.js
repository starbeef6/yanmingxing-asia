// Public chat address only. Model credentials stay on the private backend.
(() => {
  if (!window.XiongDayeChat?.endpoint) {
    window.XiongDayeChat = Object.freeze({endpoint: "https://relocation-exception-cultures-reform.trycloudflare.com/api/chat"});
  }
})();
