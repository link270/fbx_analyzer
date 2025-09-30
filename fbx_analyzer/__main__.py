"""Enable `python -m fbx_analyzer` entry point."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
