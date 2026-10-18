const toggle = document.getElementById("menuToggle");
if (new URLSearchParams(location.search).get('embedded') === '1') {
  document.body.classList.add('embedded-camera');
  document.querySelector('.sitebar').hidden = true;
}
const menu = document.getElementById("appMenu");
const closeMenu = () => { menu.hidden = true; toggle.setAttribute("aria-expanded", "false"); };
toggle.addEventListener("click", () => {
  menu.hidden = !menu.hidden;
  toggle.setAttribute("aria-expanded", String(!menu.hidden));
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".navigation")) closeMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !menu.hidden) { closeMenu(); toggle.focus(); }
});

const cameraView = document.getElementById("cameraView");
if (cameraView) {
  const logsView = document.getElementById("logsView");
  const frame = document.getElementById("logsFrame");
  window.addEventListener("fallwatch:event-saved", () => {
    frame.contentWindow?.postMessage({ type: "fallwatch:event-saved" }, location.origin);
  });
  const navigate = (view) => {
    const logs = view === "logs";
    // A display:none video may be paused by the browser. Keep the camera
    // rendered behind the log panel so frame analysis continues.
    cameraView.classList.toggle("behind-logs", logs);
    cameraView.inert = logs;
    cameraView.setAttribute("aria-hidden", String(logs));
    logsView.hidden = !logs;
    if (logs && !frame.src) frame.src = "/viewer/?embedded=1";
    document.getElementById("pageName").textContent = logs ? "Event log" : "Camera";
    menu.querySelectorAll("[data-view]").forEach((button) => {
      if (button.dataset.view === view) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    closeMenu();
  };
  menu.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => {
    location.hash = button.dataset.view === "logs" ? "logs" : "camera";
    navigate(button.dataset.view);
  }));
  window.addEventListener("hashchange", () => navigate(location.hash === "#logs" ? "logs" : "camera"));
  navigate(location.hash === "#logs" ? "logs" : "camera");
} else if (new URLSearchParams(location.search).get("embedded") === "1") {
  document.querySelector(".sitebar").hidden = true;
}

if (new URLSearchParams(location.search).get('embedded') === '1') {
  const reportHeight = () => window.parent.postMessage({type:'fallwatch:camera-height',height:Math.ceil(document.body.getBoundingClientRect().height)+32},location.origin);
  new ResizeObserver(reportHeight).observe(document.body);
  reportHeight();
}
