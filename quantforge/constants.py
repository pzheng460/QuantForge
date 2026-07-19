"""Secrets/settings loading for exchange API keys.

The only public name consumers use is ``settings`` (a Dynaconf instance
backed by ``.keys/settings.toml`` + ``.keys/.secrets.toml``).
"""

import os
import sys

from dynaconf import Dynaconf


def is_sphinx_build():
    return "sphinx" in sys.modules


if not os.path.exists(".keys/"):
    os.makedirs(".keys/", mode=0o700)
if not os.path.exists(".keys/.secrets.toml") and not is_sphinx_build():
    raise FileNotFoundError(
        "Config file not found, please create a config file at .keys/.secrets.toml"
    )


def _check_secrets_file_perms() -> None:
    """Warn (and try to fix) if the secrets file is readable by group/others.

    API keys with trade/withdraw scopes are real money; a 644 file leaks them
    to any process running as the same user's group.
    """
    if is_sphinx_build():
        return
    path = ".keys/.secrets.toml"
    if not os.path.exists(path):
        return
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:  # any group or world bits set
        print(
            f"\033[33m[secrets] WARNING: {path} mode is {oct(mode)} — "
            f"tightening to 0600 (was group/world readable)\033[0m",
            file=sys.stderr,
        )
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            print(f"\033[31m[secrets] chmod 0600 failed: {exc}\033[0m", file=sys.stderr)


_check_secrets_file_perms()


settings = Dynaconf(
    envvar_prefix="QUANTFORGE",
    settings_files=[".keys/settings.toml", ".keys/.secrets.toml"],
    warn_dynaconf_global_settings=True,
    environments=False,
    load_dotenv=True,
)
