"""Closed error taxonomy: every failure has a class, an exit code, and a next step.

Don't collapse everything into "failed". Each error prints::

    Error [E_PRESET_UNKNOWN]: no preset named 'her'
      Next: fileflow presets    (did you mean: hero?)

Exit codes: 1 system / I/O, 2 usage or config, 3 security refusal, 4 network (incl. a model provider).
"""

import click

EXIT_CODES = {
    "E_IO": 1,
    "E_USAGE": 2,
    "E_CONFIG_PARSE": 2,
    "E_CONFIG_SCHEMA": 2,
    "E_PRESET_UNKNOWN": 2,
    "E_PRESET_CYCLE": 2,
    "E_PRESET_PATH_MISSING": 2,
    "E_PLACEHOLDER_UNRESOLVED": 2,
    "E_CHECK_FAILED": 2,
    "E_PATH_OUTSIDE_ROOT": 3,
    "E_SECRET_FOUND": 3,
    "E_FETCH_NETWORK": 4,
    "E_FETCH_TIMEOUT": 4,
    "E_FETCH_BLOCKED": 4,
    "E_FETCH_EXTRACT": 4,
    "E_MODEL_CONFIG": 2,
    "E_MODEL_NOKEY": 2,
    "E_MODEL_CONSENT": 2,
    "E_MODEL_AUTH": 4,
    "E_MODEL_RATE": 4,
    "E_MODEL_NETWORK": 4,
    "E_MODEL_TIMEOUT": 4,
    "E_MODEL_RESPONSE": 4,
}


class FileflowError(click.ClickException):
    """A classified failure. ``hint`` is the next command or fix to try."""

    def __init__(self, code, message, hint=None):
        if code not in EXIT_CODES:
            raise ValueError("unknown error code: %s" % code)
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.exit_code = EXIT_CODES[code]

    def show(self, file=None):
        click.echo(click.style("Error [%s]: %s" % (self.code, self.message), fg="red"), err=True)
        if self.hint:
            click.echo("  Next: %s" % self.hint, err=True)
