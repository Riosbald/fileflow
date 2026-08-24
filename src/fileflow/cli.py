"""fileflow command line interface.

This module must import cleanly WITHOUT any server dependency
(fastapi/uvicorn): server imports are deferred inside the ``serve``
command, and the config reader is deferred inside
``read_project_config``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .core import DEFAULT_OPTIONS, FORMATS, RenderOptions, build_prompt


def read_project_config(root: Path | str) -> dict:
    """Read ``.files-to-prompt`` at *root* and map it onto CLI option
    overrides.  Returns {} when no config file exists.

    Uses a deferred import of :mod:`fileflow.config` (shared with the
    server) so this module stays server-dependency free.
    """
    from .config import config_to_options, read_config

    return config_to_options(read_config(root))


def _config_root(paths: tuple[str, ...]) -> Path:
    """The directory whose .files-to-prompt governs this invocation."""
    if paths:
        first = Path(paths[0])
        return first if first.is_dir() else first.parent
    return Path.cwd()


@click.group()
@click.version_option(package_name="fileflow")
def cli() -> None:
    """Flatten a codebase into an LLM-ready prompt."""


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--format", "fmt", type=click.Choice(FORMATS), default=None,
    help="Output format (default: 'default', or whatever the config sets).",
)
@click.option("--separators/--no-separators", default=None,
              help="Heavy visual separators between files (default format only).")
@click.option("-n", "--line-numbers/--no-line-numbers", default=None,
              help="Prefix file contents with line numbers.")
@click.option("--max-tokens", type=int, default=None,
              help="Approximate token budget; extra files are omitted.")
@click.option("--include-hidden/--no-include-hidden", default=None,
              help="Include dotfiles and dot-directories.")
@click.option("--ignore-gitignore/--no-ignore-gitignore", default=None,
              help="Do not honor .gitignore files.")
@click.option("-e", "--exclude", "exclude", multiple=True,
              help="fnmatch pattern to exclude (repeatable; merged with config patterns).")
@click.option("-o", "--output", "output", type=click.Path(dir_okay=False), default=None,
              help="Write the prompt to a file instead of stdout.")
def prompt(paths, fmt, separators, line_numbers, max_tokens,
           include_hidden, ignore_gitignore, exclude, output) -> None:
    """Render PATHS (files or directories) into a single prompt.

    Precedence: CLI flags > .files-to-prompt config > defaults.
    """
    if not paths:
        paths = (".",)

    options = RenderOptions(**DEFAULT_OPTIONS)
    options = options.merged(read_project_config(_config_root(paths)))
    options = options.merged({
        "format": fmt,
        "separators": separators,
        "line_numbers": line_numbers,
        "max_tokens": max_tokens,
        "include_hidden": include_hidden,
        "ignore_gitignore": ignore_gitignore,
        "exclude": tuple(exclude) if exclude else None,
    })

    result = build_prompt(list(paths), options)
    if output:
        Path(output).write_text(result + "\n", encoding="utf-8")
    else:
        click.echo(result)


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8090, show_default=True, type=int)
@click.option("--root", "root", default=".", show_default=True,
              type=click.Path(exists=True, file_okay=False))
def serve(host: str, port: int, root: str) -> None:
    """Serve the web client + API for ROOT (requires the server extra)."""
    try:
        import uvicorn

        from .server import create_app
    except ImportError as exc:  # pragma: no cover
        raise click.ClickException(
            f"server dependencies missing ({exc}); install fileflow[server]"
        )
    uvicorn.run(create_app(Path(root).resolve()), host=host, port=port)


def main() -> None:  # pragma: no cover
    cli(prog_name="fileflow")


if __name__ == "__main__":  # pragma: no cover
    main()
