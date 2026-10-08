"""Credentials inside URLs (found by the first-run walkthrough, FN-014).

`fileflow .` on a project with `DATABASE_URL=postgres://user:pw@host/db` warned about
a Stripe key in the same file and said nothing about the database password. Fixtures
are assembled from parts so this file is not itself credential-shaped (the repo is
scanned by `fileflow check --strict`).
"""
import time

import pytest
from click.testing import CliRunner

from fileflow import secretscan
from fileflow.cli import cli


def url(scheme, user, pw, host="db.internal:5432/prod"):
    return "%s://%s%s%s@%s" % (scheme, user, ":", pw, host)


PW = "S3cr3t" + "P4ssw0rd"

MUST_FIND = {
    "postgres": "DATABASE_URL=" + url("postgres", "admin", PW),
    "mongodb+srv": "MONGO=" + url("mongodb+srv", "app", "hunter" + "2hunter2", "cluster0.abcde.mongodb.net/db"),
    "redis, empty user": "REDIS_URL=" + url("redis", "", "long" + "password123", "cache.internal:6379/0"),
    "https remote": "url = " + url("https", "deploy", "Xk39dk" + "Q2mZ", "git.example.org/org/repo.git"),
    "quoted yaml": '  dsn: "' + url("mysql", "root", "Tr0ub4" + "dor&3", "10.0.0.5/app") + '"',
    "amqp": "BROKER=" + url("amqps", "svc", "Zq81" + "mWk2pL", "mq.internal/vhost"),
}

MUST_NOT_FIND = {
    "no credentials": "DATABASE_URL=postgres://db.internal:5432/prod",
    "env template": "DATABASE_URL=" + url("postgres", "user", "${DB_PASS}", "db/prod"),
    "shell var": "DATABASE_URL=" + url("postgres", "user", "$DB_PASS", "db/prod"),
    "placeholder word": "DATABASE_URL=" + url("postgres", "user", "password", "localhost/db"),
    "angle placeholder": url("postgres", "user", "<password>", "host/db"),
    "python % format": "u = 'postgres://%s:%s@%s/db' % (user, pw, host)",
    "too short to be a secret": url("http", "user", "pass", "host"),
    "ports only": "see http://localhost:8080/docs and https://example.com:443/x",
    "sentry dsn (public by design)": "dsn=https://0123456789abcdef0123456789abcdef@o1.ingest.sentry.io/2",
    "ssh url": "git clone ssh://git@github.com:22/org/repo.git",
    "scp-style remote": "git clone git@github.com:org/repo.git",
    "email address": "contact: ada@example.com",
    "default credentials": url("ftp", "anonymous", "anonymous", "ftp.example.org"),
    "weak default": url("postgres", "postgres", "postgres", "localhost/db"),
}


@pytest.mark.parametrize("name", sorted(MUST_FIND))
def test_credentials_in_urls_are_found(name):
    found = secretscan.scan(MUST_FIND[name] + "\n")
    assert any(f.rule == "url-credentials" for f in found), name


@pytest.mark.parametrize("name", sorted(MUST_NOT_FIND))
def test_documentation_templates_and_public_values_are_not_findings(name):
    assert secretscan.scan(MUST_NOT_FIND[name] + "\n") == [], name


def test_the_finding_never_carries_the_value():
    for f in secretscan.scan(MUST_FIND["postgres"] + "\n"):
        assert PW not in repr(f) and f._fields == ("rule", "line")


def test_line_number_is_reported():
    text = "a=1\nb=2\n" + MUST_FIND["postgres"] + "\n"
    assert [f.line for f in secretscan.scan(text) if f.rule == "url-credentials"] == [3]


def test_vendor_token_formats():
    shapes = [
        ("npm-token", "//registry.npmjs.org/:_authToken=" + "npm_" + "a1B2c3D4e5" * 3 + "a1B2c3"),
        ("gitlab-token", "x = " + "glpat-" + "x9Y8z7W6v5" * 2),
        ("sendgrid-key", "SG." + "a" * 22 + "." + "b" * 43),
    ]
    for rule, text in shapes:
        assert rule in {f.rule for f in secretscan.scan(text + "\n")}, rule
    # right prefix, wrong shape: not a finding
    assert "npm-token" not in {f.rule for f in secretscan.scan("npm_short\n")}


def test_hostile_lines_cannot_make_the_scan_slow():
    """A minified bundle or crafted file must not stall `fileflow` (ReDoS guard)."""
    hostile = [
        "a://b:" * 40000,                      # many scheme starts, never an '@'
        "x" * 300000,                          # one huge token
        "http://" + "u" * 100000 + ":" + "p" * 100000,   # long userinfo, no '@'
        ("a://" + "b:" * 50 + "c") * 3000,
        "://" * 80000,
    ]
    for text in hostile:
        t0 = time.perf_counter()
        secretscan.scan(text + "\n")
        assert time.perf_counter() - t0 < 2.0, text[:20]


def _runner():
    try:
        return CliRunner(mix_stderr=False)  # click < 8.2
    except TypeError:
        return CliRunner()  # click >= 8.2 always separates stderr


def _project(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / ".env").write_text(MUST_FIND["postgres"] + "\n")
    return tmp_path


def test_cli_warns_about_a_database_url_in_an_included_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    r = _runner().invoke(cli, [".", "--include-hidden"])
    assert r.exit_code == 0
    assert "possible secret (url-credentials" in r.stderr and PW not in r.stderr


def test_cli_block_mode_refuses_and_exclude_mode_drops_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    blocked = _runner().invoke(cli, [".", "--include-hidden", "--secrets", "block"])
    assert blocked.exit_code == 3 and PW not in blocked.output
    dropped = _runner().invoke(cli, [".", "--include-hidden", "--secrets", "exclude"])
    assert dropped.exit_code == 0 and PW not in dropped.stdout and "print('hi')" in dropped.stdout
