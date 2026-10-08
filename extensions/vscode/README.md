# fileflow — VS Code extension

Turn the current workspace (or a selected folder) into a single LLM prompt,
directly from VS Code. This is a thin wrapper around the
[fileflow CLI](https://github.com/Riosbald/fileflow).

## Prerequisites

The `fileflow` CLI must be installed on your PATH:

```bash
pip install -e .
```

## Install (development)

```bash
cd extensions/vscode
npm install -g vsce            # or use the VS Code "vsce" task
code --install-extension ./fileflow-vscode-0.1.0.vsix
```

For a live workspace, open this folder in VS Code and press `F5` (Extension
Development Host), then run the command from the Command Palette.

## Commands

| Command | Description |
|---|---|
| **fileflow: Generate prompt for workspace** | Runs `fileflow` on the workspace/selected folder and shows the output |
| **fileflow: Copy prompt to clipboard** | Runs `fileflow` and copies the result to the clipboard |
| **fileflow: Toggle watch mode** | Enables/disables **live refresh** — re-runs `fileflow` automatically when files change |

The generate and copy commands are available from the Command Palette (and the
explorer folder context menu). Watch mode can also be enabled by setting
`fileflow.watch` to `true`; a status-bar button toggles it and shows its state.

## Settings

| Setting | Default | Description |
|---|---|---|
| `fileflow.executable` | `fileflow` | Path or name of the CLI executable |
| `fileflow.format` | `default` | Output format (`default`, `xml`, `json`) |
| `fileflow.includeHidden` | `false` | Include hidden files |
| `fileflow.ignoreGitignore` | `false` | Ignore `.gitignore` rules |
| `fileflow.maxTokens` | `0` | Token budget (`0` = disabled) |
| `fileflow.watch` | `false` | Watch the workspace and auto-refresh the prompt on file changes |

## Architecture

This is a thin client. It shells out to the CLI (`child_process.execFile`) and
renders output into an output channel. Keeping the logic in the CLI avoids a
second implementation of the engine — the same golden-contract principle as the
web client. See [`docs/architecture.md`](../../docs/architecture.md).
