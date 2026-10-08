"""Secret guard: find credentials in text that is about to be pasted into an AI tool.

Design rules (see docs/product-roadmap.md, F8):

* It scans the **exact text that would be emitted**, not just file names.
* **High-confidence patterns only.** A noisy scanner gets switched off; a quiet
  one gets used. Placeholders (``changeme``, ``your_key_here``, ``${VAR}``) are
  not findings.
* A finding carries the rule name and line number, **never the secret value**,
  so the warning itself cannot leak what it is warning about.
* This *reduces* risk, it does not *prove* absence. The receipt therefore lists
  the scan under "assumed", not "verified".
"""

import re
from collections import namedtuple

Finding = namedtuple("Finding", ["rule", "line"])

# warn    report findings, keep the file in the prompt
# exclude leave files with findings out of the prompt, report, carry on
# block   fail closed: refuse to produce a prompt at all (CLI exit 3)
# off     do not scan
MODES = ("warn", "exclude", "block", "off")

# (rule name, compiled regex). Each regex matches the secret-bearing token on one line.
_PATTERNS = [
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-live-key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{24,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai-style-key", re.compile(r"\bsk-(?:proj-)?(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}\b")),
    # Distinctive, documented vendor formats (prefix + fixed shape). Not tested against live keys.
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")),
]

# `scheme://user:password@host`: the way most real credentials actually appear in .env
# files, CI config and git remotes (postgres://, mongodb+srv://, redis://, https://).
# The password class stops at `/`, whitespace, quotes and `@`, so a match is bounded and
# a hostile line cannot make the scan super-linear.
# Deliberately NOT flagged: token-only userinfo (`https://<hex>@host`): a Sentry DSN is
# public by design, and a noisy rule gets the whole scanner switched off.
_URL_CREDENTIALS = re.compile(
    r"""(?x)
    \b[A-Za-z][A-Za-z0-9+.\-]{1,30}://
    [^\s:/@'"<>`?\#]*          # user (may be empty: redis://:pw@host)
    :
    ([^\s/@'"<>`]{6,})          # password
    @[^\s/'"<>`@]+              # host
    """
)
# Passwords that are documentation, defaults or templates rather than secrets.
_WEAK_URL_PASSWORDS = frozenset({
    "password", "passwd", "secret", "changeme", "change-me", "change_me", "example", "admin123",
    "anonymous", "letmein", "123456", "1234567", "12345678", "qwerty", "postgres", "mysql",
})

# A private key is a header FOLLOWED BY a key body. A header alone is a mention in
# documentation or a test fixture, not a leaked key. The body may sit on the next line
# or after an escaped "\\n" inside a JSON/JS string, so this runs over the whole text.
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----(?:\\r|\\n|\s)*[A-Za-z0-9+/=]{16,}"
)

# `key = value` / `KEY=value` / `key: value`: the NAME must contain a sensitive *word*.
_ASSIGNMENT = re.compile(
    r"""(?x)
    ^\s*(?:(?:export|const|let|var|final|val)\s+)?
    ["']?([A-Za-z0-9_.\-]+)["']?
    \s*[:=]\s*
    ["']?([^\s"'\#]{8,})
    """
)

# Whole words, never substrings: `tokens`, `tokenizer`, `max_tokens` are LLM tokens
# (this is a prompt tool!), not credentials.
_SENSITIVE_WORDS = frozenset({"secret", "token", "password", "passwd", "apikey"})
_SENSITIVE_PAIRS = frozenset({("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key"), ("auth", "key")})

_PLACEHOLDER = re.compile(
    r"""(?ix)
    ^(?:
        change[_\-]?me | your[_\-].* | .*[_\-]here | example.* | sample.* | dummy.* | placeholder.*
        | xxx+ | \*{3,} | <.*> | \$\{.*\} | \$\(.*\) | %\(.*\)s | \{\{.*\}\} | todo | null | none | true | false
        | 0{8,} | 1234567.*
    )$
    """
)
_CODE_CHARS = set("()[]{}<>$%`\\|")
_ATTR_ACCESS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def _words(name):
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def _sensitive_name(name):
    words = _words(name)
    if any(w in _SENSITIVE_WORDS for w in words):
        return True
    return any((a, b) in _SENSITIVE_PAIRS for a, b in zip(words, words[1:]))


def _looks_like_placeholder(value):
    return bool(_PLACEHOLDER.match(value.strip()))


def _plausible_secret(value):
    """High-confidence only: a code expression or a bare constant is not a credential."""
    value = value.rstrip(",;")
    if not value or _looks_like_placeholder(value):
        return False
    if any(c in _CODE_CHARS for c in value) or _ATTR_ACCESS.match(value):
        return False  # call, attribute access, template, array...
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    mixed = any(c.islower() for c in value) and any(c.isupper() for c in value)
    # A real secret is not a plain identifier: it has digits among letters, or is long and mixed-case.
    return (has_digit and has_alpha) or (len(value) >= 24 and mixed)


def _plausible_url_password(value):
    v = value.strip()
    if _looks_like_placeholder(v) or v.lower() in _WEAK_URL_PASSWORDS:
        return False
    # `%s`, `${X}`, `{{x}}`, `$X`, `:password`-style templates and code are not credentials.
    if any(c in _CODE_CHARS for c in v) or v.startswith("$") or "%s" in v:
        return False
    return True


def scan(text):
    """Return a list of :class:`Finding` (rule, 1-based line) for ``text``.

    Findings are de-duplicated per (rule, line) and sorted by line.
    """
    found = set()
    for m in _PRIVATE_KEY.finditer(text):
        found.add(Finding("private-key", text.count("\n", 0, m.start()) + 1))
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in _PATTERNS:
            if pattern.search(line):
                found.add(Finding(rule, number))
        for um in _URL_CREDENTIALS.finditer(line):
            if _plausible_url_password(um.group(1)):
                found.add(Finding("url-credentials", number))
                break
        m = _ASSIGNMENT.match(line)
        if m and _sensitive_name(m.group(1)) and _plausible_secret(m.group(2)):
            found.add(Finding("sensitive-assignment", number))
    return sorted(found, key=lambda f: (f.line, f.rule))
