"""Disk full / read-only mid-save: the previous file survives, no partial tmp is left behind."""
import errno
import json
from pathlib import Path

import pytest

from core import config, jobs_store, shared_config


def _enospc_after_half(monkeypatch):
    real = Path.write_text

    def write_text(self, text, *a, **k):
        if self.suffix == ".tmp":
            real(self, text[: len(text) // 2], *a, **k)
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, text, *a, **k)
    monkeypatch.setattr(Path, "write_text", write_text)


@pytest.mark.parametrize("save", [config._save_json, shared_config.save_json, jobs_store._save])
def test_save_on_full_disk_keeps_previous_file(tmp_path, monkeypatch, save):
    target = tmp_path / "ü data" / "state.json"
    save(target, {"v": 1})
    _enospc_after_half(monkeypatch)
    with pytest.raises(OSError) as e:
        save(target, {"v": 2, "big": "x" * 10_000})
    assert e.value.errno == errno.ENOSPC
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}
    assert not target.with_suffix(".tmp").exists()


def test_data_dir_problem_reports_unwritable(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYQUEUE_DATA_DIR", str(tmp_path / "ok"))
    assert config.data_dir_problem() is None
    _enospc_after_half(monkeypatch)
    real = Path.write_text

    def boom(self, *a, **k):
        if self.name.startswith(".write-probe-"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", boom)
    msg = config.data_dir_problem()
    assert msg and "not writable" in msg and "No space left" in msg
