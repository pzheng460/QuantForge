from importlib.metadata import version, PackageNotFoundError


try:
    __version__ = version("quantforge")
except PackageNotFoundError:
    __version__ = "unknown"
