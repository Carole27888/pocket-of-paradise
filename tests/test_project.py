"""Project-level guarantees: no Node toolchain, Passenger entry point, deploy checks."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_node_toolchain():
    for name in ("package.json", "package-lock.json", "yarn.lock", "node_modules", "webpack.config.js",
                 "tailwind.config.js", "vite.config.js"):
        assert not (ROOT / name).exists(), f"{name} must not exist: the project has no Node build step"


def test_vendored_libraries_present():
    for rel in ("static/vendor/htmx/htmx.min.js", "static/vendor/alpine/alpine.min.js",
                "static/vendor/chartjs/chart.umd.min.js", "static/fonts/manrope-latin-400-normal.woff2"):
        assert (ROOT / rel).stat().st_size > 1000, rel


def _env(**extra):
    env = dict(os.environ)
    env.update({"SECRET_KEY": "x" * 20 + "-prod-test-key-9f8e7d6c5b4a3210zyxwvutsrq", "ALLOWED_HOSTS": "example.com",
                "DATABASE_URL": f"sqlite:///{ROOT / 'tmp' / 'deploycheck.sqlite3'}", "DEBUG": "False"})
    env.update(extra)
    return env


def test_passenger_wsgi_exposes_application():
    code = ("import runpy; ns = runpy.run_path('passenger_wsgi.py'); "
            "assert callable(ns['application']); print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=_env(DJANGO_SETTINGS_MODULE="days_safaris.settings.prod"),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_check_deploy_passes_with_prod_settings():
    r = subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"], cwd=ROOT,
                       env=_env(DJANGO_SETTINGS_MODULE="days_safaris.settings.prod"),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_prod_refuses_dev_secret_key():
    env = _env(DJANGO_SETTINGS_MODULE="days_safaris.settings.prod")
    env.pop("SECRET_KEY")
    r = subprocess.run([sys.executable, "-c", "import django; django.setup()"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr


def test_collectstatic_with_manifest_storage(tmp_path):
    """WhiteNoise's manifest storage fails on broken url()/sourcemap references — catch that before deploy."""
    r = subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput", "-v", "0"], cwd=ROOT,
                       env=_env(DJANGO_SETTINGS_MODULE="days_safaris.settings.prod", STATIC_ROOT=str(tmp_path)),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "staticfiles.json").exists()
