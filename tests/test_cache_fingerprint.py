import os
import time

import pytest

from lib.cache_fingerprint import fingerprint_files, reporting_inputs_fingerprint


def test_fingerprint_changes_when_file_content_changes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("version 1")
    fp1 = fingerprint_files([str(f)])

    time.sleep(0.01)
    f.write_text("version 2 - different size")
    fp2 = fingerprint_files([str(f)])

    assert fp1 != fp2


def test_fingerprint_stable_when_nothing_changes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("stable contents")
    fp1 = fingerprint_files([str(f)])
    fp2 = fingerprint_files([str(f)])
    assert fp1 == fp2


def test_fingerprint_order_independent(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("one")
    f2.write_text("two")
    assert fingerprint_files([str(f1), str(f2)]) == fingerprint_files([str(f2), str(f1)])


def test_fingerprint_changes_when_a_file_disappears(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("here")
    fp_present = fingerprint_files([str(f)])
    os.remove(f)
    fp_missing = fingerprint_files([str(f)])
    assert fp_present != fp_missing


def test_fingerprint_never_crashes_on_a_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.parquet"
    fp = fingerprint_files([str(missing)])
    assert isinstance(fp, str) and fp


def test_reporting_inputs_fingerprint_is_a_stable_short_string():
    fp1 = reporting_inputs_fingerprint()
    fp2 = reporting_inputs_fingerprint()
    assert fp1 == fp2
    assert isinstance(fp1, str)
    assert len(fp1) == 16


def test_reporting_inputs_fingerprint_reacts_to_a_tracked_data_file_changing(tmp_path, monkeypatch):
    import lib.cache_fingerprint as cache_fingerprint

    monkeypatch.setattr(cache_fingerprint, "DATA_DIR", str(tmp_path))
    tracked = tmp_path / "metadata.json"
    tracked.write_text("{}")
    fp1 = cache_fingerprint.reporting_inputs_fingerprint()

    time.sleep(0.01)
    tracked.write_text('{"season": 2025}')
    fp2 = cache_fingerprint.reporting_inputs_fingerprint()

    assert fp1 != fp2
