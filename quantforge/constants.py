"""Secrets/settings loading for exchange API keys.

The only public name consumers use is ``settings`` (a Dynaconf instance
backed by ``.keys/settings.toml`` + ``.keys/.secrets.toml``).

All paths are resolved relative to this package (``<repo-root>/.keys``), so
the module import has no working-directory side effects: importing
``quantforge`` from anywhere must never create or read ``.keys`` under the
caller's CWD.
"""

import sys
from pathlib import Path

from dynaconf import Dynaconf

# <repo-root>/quantforge/constants.py -> <repo-root>/.keys
_KEYS_DIR = Path(__file__).resolve().parent.parent / ".keys"
_SETTINGS_FILE = _KEYS_DIR / "settings.toml"
_SECRETS_FILE = _KEYS_DIR / ".secrets.toml"


def is_sphinx_build():
    return "sphinx" in sys.modules


if not _KEYS_DIR.exists():
    _KEYS_DIR.mkdir(mode=0o700)
if not _SECRETS_FILE.exists() and not is_sphinx_build():
    raise FileNotFoundError(
        "Config file not found, please create a config file at "
        f"{_SECRETS_FILE}"
    )


def _check_secrets_file_perms() -> None:
    """Warn (and try to fix) if the secrets file is readable by group/others.

    API keys with trade/withdraw scopes are real money; a 644 file leaks them
    to any process running as the same user's group.
    """
    if is_sphinx_build():
        return
    if not _SECRETS_FILE.exists():
        return
    mode = _SECRETS_FILE.stat().st_mode & 0o777
    if mode & 0o077:  # any group or world bits set
        print(
            f"\033[33m[secrets] WARNING: {_SECRETS_FILE} mode is {oct(mode)} — "
            f"tightening to 0600 (was group/world readable)\033[0m",
            file=sys.stderr,
        )
        try:
            _SECRETS_FILE.chmod(0o600)
        except OSError as exc:
            print(
                f"\033[31m[secrets] chmod 0600 failed: {exc}\033[0m",
                file=sys.stderr,
            )


_check_secrets_file_perms()


settings = Dynaconf(
    envvar_prefix="QUANTFORGE",
    settings_files=[str(_SETTINGS_FILE), str(_SECRETS_FILE)],
    warn_dynaconf_global_settings=True,
    environments=False,
    load_dotenv=True,
)
