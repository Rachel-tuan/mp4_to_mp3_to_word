"""FFmpeg service tests. We mock subprocess.run so these run on any OS
without a real ffmpeg binary -- see README for an optional real-ffmpeg
smoke test.

Covers spec 十五 items: 1 (normal), 3 (no audio track), 4 (output exists),
5/6/7 (unicode / spaces / parentheses in filenames), 8 (ffmpeg failure).
"""
import os
import subprocess
import tempfile
from unittest import mock

import pytest

from services.ffmpeg_service import FFmpegError, FFmpegService, OverwritePolicy


def _fake_completed(returncode=0, stderr=b""):
    return subprocess.CompletedProcess(args=["ffmpeg"], returncode=returncode, stdout=b"", stderr=stderr)


@pytest.fixture
def tmp_input(tmp_path):
    p = tmp_path / "input.mp4"
    p.write_bytes(b"fake mp4 bytes")
    return str(p)


def test_normal_conversion_success(tmp_path, tmp_input):
    output = str(tmp_path / "out.mp3")
    svc = FFmpegService(ffmpeg_path="ffmpeg")

    def fake_run(cmd, stdout, stderr, creationflags):
        # simulate ffmpeg writing the output file
        with open(cmd[-1], "wb") as f:
            f.write(b"id3-ish mp3 bytes")
        return _fake_completed(returncode=0)

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = svc.extract_mp3(tmp_input, output)
    assert result.output_path == os.path.abspath(output)
    assert not result.skipped
    assert os.path.isfile(output)


def test_no_audio_track_raises(tmp_path, tmp_input):
    """ffmpeg exits 0 but produces an empty file (e.g. video has no audio)."""
    output = str(tmp_path / "out.mp3")
    svc = FFmpegService(ffmpeg_path="ffmpeg")

    def fake_run(cmd, stdout, stderr, creationflags):
        open(cmd[-1], "wb").close()  # empty file
        return _fake_completed(returncode=0)

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(FFmpegError):
            svc.extract_mp3(tmp_input, output)


def test_output_exists_skip_policy(tmp_path, tmp_input):
    output = tmp_path / "out.mp3"
    output.write_bytes(b"already here")
    svc = FFmpegService(ffmpeg_path="ffmpeg")

    with mock.patch("subprocess.run") as run_mock:
        result = svc.extract_mp3(tmp_input, str(output), overwrite=OverwritePolicy.SKIP)
    run_mock.assert_not_called()
    assert result.skipped


def test_output_exists_fail_policy(tmp_path, tmp_input):
    output = tmp_path / "out.mp3"
    output.write_bytes(b"already here")
    svc = FFmpegService(ffmpeg_path="ffmpeg")
    with pytest.raises(FFmpegError):
        svc.extract_mp3(tmp_input, str(output), overwrite=OverwritePolicy.FAIL)


@pytest.mark.parametrize(
    "filename",
    [
        "中文视频文件.mp4",
        "video with spaces.mp4",
        "video (final version).mp4",
    ],
)
def test_special_filenames(tmp_path, filename):
    input_path = tmp_path / filename
    input_path.write_bytes(b"fake mp4")
    output = tmp_path / (os.path.splitext(filename)[0] + ".mp3")
    svc = FFmpegService(ffmpeg_path="ffmpeg")

    def fake_run(cmd, stdout, stderr, creationflags):
        assert cmd[-1] == str(output.resolve()) or os.path.abspath(cmd[-1]) == os.path.abspath(str(output))
        with open(cmd[-1], "wb") as f:
            f.write(b"mp3 bytes")
        return _fake_completed(returncode=0)

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = svc.extract_mp3(str(input_path), str(output))
    assert os.path.isfile(result.output_path)


def test_ffmpeg_failure_returncode(tmp_path, tmp_input):
    output = str(tmp_path / "out.mp3")
    svc = FFmpegService(ffmpeg_path="ffmpeg")

    def fake_run(cmd, stdout, stderr, creationflags):
        return _fake_completed(returncode=1, stderr=b"Invalid data found when processing input")

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(FFmpegError) as exc_info:
            svc.extract_mp3(tmp_input, output)
    assert exc_info.value.returncode == 1
    assert "Invalid data" in exc_info.value.stderr


def test_missing_input_file(tmp_path):
    svc = FFmpegService(ffmpeg_path="ffmpeg")
    with pytest.raises(FFmpegError):
        svc.extract_mp3(str(tmp_path / "does_not_exist.mp4"), str(tmp_path / "out.mp3"))
