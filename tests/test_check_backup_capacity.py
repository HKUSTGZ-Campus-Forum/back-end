from pathlib import Path

import pytest

from tools import check_backup_capacity as capacity


@pytest.mark.parametrize("raw", ["", "-1", "+1", " 1", "1 ", "1.0", "１"])
def test_parse_nonnegative_decimal_rejects_non_ascii_decimal(raw):
    with pytest.raises(capacity.CapacityCheckError):
        capacity.parse_nonnegative_decimal(raw, field="value")


def test_required_capacity_uses_unbounded_integer_arithmetic():
    huge = 10**100
    assert capacity.required_capacity(payload_bytes=huge, reserve_bytes=huge) == 2 * huge


def test_check_capacity_rejects_insufficient_huge_requirement(monkeypatch, tmp_path):
    monkeypatch.setattr(capacity, "available_capacity", lambda _path: 10**100)

    with pytest.raises(capacity.CapacityCheckError, match="Insufficient disk"):
        capacity.check_capacity(
            path=tmp_path,
            payload_bytes=10**100,
            reserve_bytes=1,
            purpose="the test backup",
        )


def test_main_fails_closed_when_capacity_probe_fails(monkeypatch, tmp_path, capsys):
    def fail_probe(_path: Path) -> int:
        raise OSError("probe failed")

    monkeypatch.setattr(capacity, "available_capacity", fail_probe)

    assert capacity.main(
        [
            "--path",
            str(tmp_path),
            "--payload-bytes",
            "1",
            "--reserve-bytes",
            "1",
            "--purpose",
            "the test backup",
        ]
    ) == 1
    assert "probe failed" in capsys.readouterr().err


def test_available_capacity_opens_directory_without_following_symlinks(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    assert capacity.available_capacity(target) > 0
    with pytest.raises(OSError):
        capacity.available_capacity(link)
