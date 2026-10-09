"""Secret scanner precision: real code must not trip it; real credentials must.

The false-positive cases are the exact lines the scanner wrongly flagged when fileflow
was first pointed at its own repository (dogfooding): `token` matched inside `tokens` /
`tokenizer` (LLM tokens, not credentials), code expressions looked like values, and a
header-only private-key string in a test fixture looked like a leaked key.
"""
import pytest

from fileflow.secretscan import scan

AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
SLACK = "xox" + "b-1234567890-abcdefghij"
BEGIN = "-----BEGIN "
BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7"
GH = "ghp_" + "a1B2c3D4" * 5


@pytest.mark.parametrize("line", [
    "tokens = count_tokens(content, tokenizer)",
    'tokenizer="heuristic",',
    "secrets=secrets_mode,",
    "token = _secrets.token_urlsafe(16)",
    'max_tokens = int(out.get("max_tokens", 0) or 0)',
    'SECRET_BLOCKED = "SECRET_BLOCKED"',
    'TOKEN_BUDGET = "TOKEN_BUDGET"',
    'maxTokens: parseInt($("#opt-max-tokens").value, 10) || 0,',
    "token = m.group(1)",
    "password = getpass.getpass()",
    "api_key = os.environ['API_KEY']",
    "PASSWORD_MIN_LENGTH = 12",
    "secret_name = some_variable_name",
    "auth_token = request.headers.get('x')",
    "DB_PASSWORD=changeme",
    "API_KEY=your_api_key_here",
    "password: ${DB_PASSWORD}",
    "# set TOKEN=<your token> before running",
    "const sk = 'skeleton-key-for-the-css-class-name-here'",
])
def test_ordinary_code_and_placeholders_are_not_findings(line):
    assert scan(line) == [], line


@pytest.mark.parametrize("line,rule", [
    ("export DB_PASSWORD=hunter2hunter2", "sensitive-assignment"),
    ('password: "s3cr3t-value-123"', "sensitive-assignment"),
    ("API_KEY=sk_live_51Habc123def456", "sensitive-assignment"),
    ('const apiKey = "abc123XYZ789qwerty";', "sensitive-assignment"),
    ("client_secret = 'Zx9Qm2Lk7Pv4Rt8W'", "sensitive-assignment"),
    ("aws = %s" % AWS, "aws-access-key-id"),
    ("token = %s" % GH, "github-token"),
    ("webhook = %s" % SLACK, "slack-token"),
])
def test_real_looking_credentials_are_findings(line, rule):
    assert rule in {f.rule for f in scan(line)}, line


def test_private_key_needs_a_body_not_just_a_header():
    pem = BEGIN + "RSA PRIVATE KEY-----\n" + BODY + "\n-----END RSA PRIVATE KEY-----"
    assert [f.rule for f in scan(pem)] == ["private-key"]
    as_json = '{"key": "' + BEGIN + "PRIVATE KEY-----" + "\\n" + BODY + '"}'   # escaped newline inside a JSON string
    assert [f.rule for f in scan(as_json)] == ["private-key"]
    assert scan(BEGIN + "RSA PRIVATE KEY-----") == []                      # a mention / truncated header
    assert scan("Use `" + BEGIN + "PRIVATE KEY-----` as the marker.") == []
    assert scan('("' + BEGIN + 'RSA PRIVATE KEY-----\\n", True),') == []   # test fixture, header only


def test_private_key_finding_reports_the_header_line():
    text = "intro\n\n" + BEGIN + "OPENSSH PRIVATE KEY-----\n" + BODY + "\n"
    assert [(f.rule, f.line) for f in scan(text)] == [("private-key", 3)]


def test_findings_never_contain_the_value():
    for f in scan("password: hunter2hunter2\n"):
        assert "hunter2" not in repr(f)


def test_word_boundaries_in_key_names():
    # `token` as a word is sensitive; `tokens` / `tokenizer` are not.
    assert scan("auth_token = 'abc123def456ghi'")
    assert scan("authToken = 'abc123def456ghi'")
    assert scan("TOKEN='abc123def456ghi'")
    assert not scan("tokens = 'abc123def456ghi'")
    assert not scan("tokenizer = 'abc123def456ghi'")
    assert not scan("max_tokens = 'abc123def456ghi'")


def test_scanning_our_own_source_is_quiet():
    """Regression for the dogfooding finding: no findings in fileflow's own source tree."""
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    noisy = []
    for pattern in ("fileflow/**/*.py", "fileflow/web/js/*.js", "tests/*.py", "testdata/*.py"):
        for path in glob.glob(os.path.join(root, pattern), recursive=True):
            with open(path, encoding="utf-8") as fh:
                for f in scan(fh.read()):
                    noisy.append((os.path.relpath(path, root), f.rule, f.line))
    assert noisy == [], noisy
