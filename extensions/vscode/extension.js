// fileflow VS Code extension.
//
// Wraps the `fileflow` CLI: builds the command from the current configuration,
// runs it against the workspace folder (or the selected folder), and shows the
// result in an output channel / copies it to the clipboard. An optional watch
// mode re-runs fileflow automatically when files change (watch-driven live
// refresh) — the same live-reload idea as `fileflow serve`'s /api/watch.
//
// The CLI must be installed (`pip install -e .`) for these commands to run.

const vscode = require("vscode");
const { execFile } = require("child_process");
const { promisify } = require("util");

const execFileP = promisify(execFile);

/** @type {vscode.OutputChannel} */
let channel = null;

/** @type {vscode.FileSystemWatcher|null} */
let watcher = null;

let debounceTimer = null;

function getChannel() {
  if (!channel) channel = vscode.window.createOutputChannel("fileflow");
  return channel;
}

/** Build the CLI argument list for the given folder from the config. */
function buildArgs(folder, cfg) {
  const args = [];
  if (cfg.get("includeHidden")) args.push("--include-hidden");
  if (cfg.get("ignoreGitignore")) args.push("--ignore-gitignore");
  const format = cfg.get("format");
  if (format && format !== "default") args.push("--format", format);
  const maxTokens = cfg.get("maxTokens");
  if (maxTokens && maxTokens > 0) args.push("--max-tokens", String(maxTokens));
  args.push(folder.uri ? folder.uri.fsPath : folder.fsPath);
  return args;
}

/** Run `fileflow <args>` and return { stdout, stderr }. */
async function runFileflow(args) {
  const cfg = vscode.workspace.getConfiguration("fileflow");
  const executable = cfg.get("executable", "fileflow");
  try {
    const { stdout, stderr } = await execFileP(executable, args, {
      maxBuffer: 50 * 1024 * 1024,
    });
    return { stdout, stderr };
  } catch (err) {
    const detail =
      err && err.code === "ENOENT"
        ? "The fileflow CLI is not installed. Run: pip install -e ."
        : (err && err.message) || String(err);
    throw new Error(detail);
  }
}

/** Run fileflow for `folder` and render into the output channel. */
async function runAndShow(folder) {
  const cfg = vscode.workspace.getConfiguration("fileflow");
  const out = getChannel();
  const label = (folder && (folder.name || folder.uri.fsPath)) || "workspace";
  out.clear();
  out.appendLine(`fileflow: ${label}${watch.enabled ? " (watch on)" : ""}\n`);
  try {
    const { stdout, stderr } = await runFileflow(buildArgs(folder, cfg));
    out.append(stdout);
    if (stderr) out.appendLine("\n[stderr]\n" + stderr);
  } catch (err) {
    out.appendLine("[error] " + err.message);
    vscode.window.showErrorMessage(err.message);
  }
  out.show(true);
}

function pickFolder() {
  const workspaceFolders = vscode.workspace.workspaceFolders || [];
  const selected = vscode.window.activeTextEditor
    ? vscode.workspace.getWorkspaceFolder(vscode.window.activeTextEditor.document.uri)
    : undefined;
  if (selected) return Promise.resolve(selected);
  if (workspaceFolders.length === 1) return Promise.resolve(workspaceFolders[0]);
  return vscode.window.showWorkspaceFolderPick({ placeHolder: "Choose a folder" });
}

/* ---------- Watch mode (live refresh) ---------- */
const watch = { enabled: false, folder: null };

/** True if a changed URI is inside .git (to avoid noisy refreshes). */
function isGitInternal(uri) {
  const p = uri.fsPath.replace(/\\/g, "/");
  return p.split("/").some((seg) => seg === ".git");
}

function scheduleRefresh() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    if (watch.enabled && watch.folder) runAndShow(watch.folder);
  }, 500);
}

function enableWatch(folder) {
  if (watcher) return;
  const pattern = new vscode.RelativePattern(folder.uri ? folder.uri : folder, "**/*");
  watcher = vscode.workspace.createFileSystemWatcher(pattern);
  const onChange = (uri) => {
    if (!isGitInternal(uri)) scheduleRefresh();
  };
  watcher.onDidCreate(onChange);
  watcher.onDidChange(onChange);
  watcher.onDidDelete(onChange);
  watch.enabled = true;
  watch.folder = folder;
}

function disableWatch() {
  clearTimeout(debounceTimer);
  if (watcher) {
    watcher.dispose();
    watcher = null;
  }
  watch.enabled = false;
  watch.folder = null;
}

/** Reflect watch state on the status bar. */
function updateStatusBar(statusItem) {
  statusItem.text = watch.enabled ? "$(radio-tower) fileflow: watch on" : "$(radio-tower) fileflow: watch off";
  statusItem.tooltip = watch.enabled ? "Click to turn off watch mode" : "Click to turn on watch mode";
  statusItem.show();
}

async function toggleWatch(statusItem) {
  if (watch.enabled) {
    disableWatch();
  } else {
    const folder = await pickFolder();
    if (!folder) return;
    enableWatch(folder);
    runAndShow(folder);
  }
  updateStatusBar(statusItem);
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("fileflow.generatePrompt", async (uri) => {
      const folder = uri
        ? { uri, name: vscode.workspace.getWorkspaceFolder(uri)?.name || uri.fsPath }
        : await pickFolder();
      if (!folder) return;
      await runAndShow(folder);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("fileflow.copyPrompt", async () => {
      const folder = await pickFolder();
      if (!folder) return;
      const cfg = vscode.workspace.getConfiguration("fileflow");
      try {
        const { stdout } = await runFileflow(buildArgs(folder, cfg));
        await vscode.env.clipboard.writeText(stdout);
        vscode.window.setStatusBarMessage(
          "$(check) fileflow prompt copied to clipboard",
          3000
        );
      } catch (err) {
        vscode.window.showErrorMessage(err.message);
      }
    })
  );

  // Watch-mode toggle + status bar item.
  const statusItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100
  );
  statusItem.command = "fileflow.toggleWatch";
  updateStatusBar(statusItem);
  context.subscriptions.push(statusItem);
  context.subscriptions.push(
    vscode.commands.registerCommand("fileflow.toggleWatch", () => toggleWatch(statusItem))
  );

  // Auto-enable watch if configured.
  if (vscode.workspace.getConfiguration("fileflow").get("watch")) {
    pickFolder().then((folder) => {
      if (folder) {
        enableWatch(folder);
        runAndShow(folder);
        updateStatusBar(statusItem);
      }
    });
  }

  context.subscriptions.push({ dispose: disableWatch });
}

function deactivate() {
  disableWatch();
}

module.exports = { activate, deactivate };
