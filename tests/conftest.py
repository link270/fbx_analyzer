import os
from pathlib import Path
from typing import Iterator

import pytest


def has_fbx_sdk() -> bool:
    try:
        import fbx  # type: ignore
        import FbxCommon  # type: ignore
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def data_dir() -> Path:
    root = Path(__file__).resolve().parent
    return root / "files"


@pytest.fixture(scope="session")
def keep_outputs_flag() -> bool:
    return os.getenv("FBX_ANALYZER_KEEP_TEST_OUTPUTS", "0") not in {"", "0", "false", "False"}


@pytest.fixture
def tmp_outputs(tmp_path: Path) -> Iterator[Path]:
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    yield out
    # Deletion is handled by pytest's tmp_path cleanup

