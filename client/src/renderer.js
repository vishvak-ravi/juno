const { electron, chrome, node } = window.juno.versions;
document.getElementById("versions").textContent =
  `Electron ${electron} · Chromium ${chrome} · Node ${node}`;
