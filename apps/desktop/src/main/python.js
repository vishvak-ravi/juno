// The checkout's Python, the .venv the recorder runs from; the main process
// also asks it for this computer's device id and recorder settings.
const { execFile } = require("node:child_process");
const path = require("node:path");

const checkoutPython = (repoDir) => path.join(repoDir, ".venv/bin/python");

// Runs the checkout's Python with `args`, `input` on its stdin and `env`;
// resolves to {stdout, stderr, error} once it has exited, error being execFile's
// (the exit status, or why it could not start) or null.
function runPython(repoDir, args, { env = process.env, input = "" } = {}) {
  return new Promise((resolve) => {
    const child = execFile(checkoutPython(repoDir), args, { cwd: repoDir, env },
      (error, stdout, stderr) => resolve({ stdout, stderr, error }));
    child.stdin.end(input);
  });
}

module.exports = { checkoutPython, runPython };
