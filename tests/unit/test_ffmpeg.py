"""Unit tests for the FFmpeg wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from instagiffer.external.ffmpeg import (
    FRAME_PATTERN,
    FFmpegError,
    FFmpegNotFoundError,
    FFmpegWrapper,
    VideoInfo,
    _duration_str_to_sec,
    _parse_codec,
    _parse_duration,
    _parse_fps,
    _parse_resolution,
)

TYPICAL_OUTPUT = """
ffmpeg version 6.1 Copyright (c) 2000-2023 the FFmpeg developers
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:00:12.50, start: 0.000000, bitrate: 4567 kb/s
    Stream #0:0(und): Video: h264 (High), yuv420p, 1920x1080, 4200 kb/s, 30 fps, 30 tbr, 90k tbn
    Stream #0:1(und): Audio: aac, 44100 Hz, stereo, fltp, 317 kb/s
"""

NON_SQUARE_OUTPUT = """
  Duration: 00:00:05.00, start: 0.000000, bitrate: 1234 kb/s
    Stream #0:0: Video: h264, yuv420p, 720x480 [SAR 32:27 DAR 16:9], 29.97 fps, 29.97 tbr
"""

ROTATED_OUTPUT = """
  Duration: 00:00:08.00, start: 0.000000, bitrate: 9999 kb/s
    Stream #0:0: Video: h264, yuv420p, 1080x1920, 30 fps
  Metadata:
    rotate          : 90
"""

NO_VIDEO_STREAM_OUTPUT = """
  Duration: 00:00:10.00, start: 0.000000, bitrate: 317 kb/s
    Stream #0:0: Audio: aac, 44100 Hz, stereo
"""

NO_DURATION_OUTPUT = """
    Stream #0:0: Video: h264, yuv420p, 640x480, 25 fps
"""


class TestDurationStrToSec:
    def test_zero(self):
        assert _duration_str_to_sec('00:00:00.00') == pytest.approx(0.0)

    def test_seconds_only(self):
        assert _duration_str_to_sec('00:00:05.50') == pytest.approx(5.5)

    def test_minutes(self):
        assert _duration_str_to_sec('00:01:30.00') == pytest.approx(90.0)

    def test_hours(self):
        assert _duration_str_to_sec('01:00:00.00') == pytest.approx(3600.0)

    def test_mixed(self):
        assert _duration_str_to_sec('01:02:03.40') == pytest.approx(3723.40)


class TestParseResolution:
    def test_typical(self):
        w, h = _parse_resolution(TYPICAL_OUTPUT)
        assert (w, h) == (1920, 1080)

    def test_non_square_pixels_corrects_width(self):
        # 720x480 with SAR 32:27 DAR 16:9
        # expected width = round(480 * 16/9) = round(853.3) = 853
        w, h = _parse_resolution(NON_SQUARE_OUTPUT)
        assert h == 480
        assert w == 853

    def test_rotation_90_swaps_dimensions(self):
        # Original: 1080x1920 → after 90° swap → 1920x1080
        w, h = _parse_resolution(ROTATED_OUTPUT)
        assert (w, h) == (1920, 1080)

    def test_no_video_stream_raises(self):
        with pytest.raises(FFmpegError, match='resolution'):
            _parse_resolution(NO_VIDEO_STREAM_OUTPUT)


class TestParseDuration:
    def test_typical(self):
        assert _parse_duration(TYPICAL_OUTPUT) == pytest.approx(12.5)

    def test_no_duration_raises(self):
        with pytest.raises(FFmpegError, match='duration'):
            _parse_duration(NO_DURATION_OUTPUT)


class TestParseFps:
    def test_prefers_fps_over_tbr(self):
        # Output has both "30 fps" and "30 tbr" — should pick fps
        assert _parse_fps(TYPICAL_OUTPUT) == pytest.approx(30.0)

    def test_falls_back_to_tbr(self):
        output = '    Stream #0:0: Video: h264, yuv420p, 1280x720, 24 tbr\n'
        assert _parse_fps(output) == pytest.approx(24.0)

    def test_warns_and_defaults_when_missing(self):
        assert _parse_fps('no fps info here') == pytest.approx(25.0)

    def test_fractional_fps(self):
        output = '    Stream #0:0: Video: h264, yuv420p, 640x480, 29.97 fps\n'
        assert _parse_fps(output) == pytest.approx(29.97)


class TestParseCodec:
    def test_h264(self):
        assert _parse_codec(TYPICAL_OUTPUT) == 'h264'

    def test_missing_returns_empty_string(self):
        assert _parse_codec('no video stream here') == ''


class TestFFmpegWrapperInit:
    def test_explicit_path_ok(self, tmp_path):
        fake_ffmpeg = tmp_path / 'ffmpeg'
        fake_ffmpeg.touch()
        wrapper = FFmpegWrapper(fake_ffmpeg)
        assert wrapper.ffmpeg == fake_ffmpeg

    def test_explicit_path_missing_raises(self, tmp_path):
        with pytest.raises(FFmpegNotFoundError):
            FFmpegWrapper(tmp_path / 'no_such_ffmpeg')

    def test_auto_locate_success(self, tmp_path):
        fake = tmp_path / 'ffmpeg'
        fake.touch()
        with patch('shutil.which', return_value=str(fake)):
            wrapper = FFmpegWrapper()
        assert wrapper.ffmpeg == fake

    def test_auto_locate_not_found_raises(self):
        with patch('shutil.which', return_value=None):
            with pytest.raises(FFmpegNotFoundError):
                FFmpegWrapper()


class TestGetVideoInfo:
    @pytest.fixture
    def wrapper(self, tmp_path):
        fake = tmp_path / 'ffmpeg'
        fake.touch()
        return FFmpegWrapper(fake)

    def test_returns_video_info(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stderr=TYPICAL_OUTPUT)
            info = wrapper.get_video_info(video)

        assert isinstance(info, VideoInfo)
        assert info.width == 1920
        assert info.height == 1080
        assert info.duration_sec == pytest.approx(12.5)
        assert info.fps == pytest.approx(30.0)
        assert info.codec == 'h264'

    def test_duration_ms_property(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stderr=TYPICAL_OUTPUT)
            info = wrapper.get_video_info(video)
        assert info.duration_ms == 12500

    def test_aspect_ratio_property(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stderr=TYPICAL_OUTPUT)
            info = wrapper.get_video_info(video)
        assert info.aspect_ratio == pytest.approx(16 / 9, rel=1e-3)

    def test_file_not_found_raises(self, wrapper, tmp_path):
        with pytest.raises(FileNotFoundError):
            wrapper.get_video_info(tmp_path / 'missing.mp4')


class TestExtractFrames:
    @pytest.fixture
    def wrapper(self, tmp_path):
        fake = tmp_path / 'ffmpeg'
        fake.touch()
        return FFmpegWrapper(fake)

    def _make_mock_process(self, stderr_lines: list[str], returncode: int = 0) -> MagicMock:
        mock = MagicMock()
        mock.stderr = iter(stderr_lines)
        mock.returncode = returncode
        mock.wait.return_value = None
        return mock

    def test_basic_extraction(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'

        # Simulate two frames being created
        def fake_popen(cmd, **kwargs):
            # Actually create the output files when Popen is called
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / (FRAME_PATTERN % 1)).touch()
            (out_dir / (FRAME_PATTERN % 2)).touch()
            return self._make_mock_process([])

        with patch('subprocess.Popen', side_effect=fake_popen):
            frames = wrapper.extract_frames(video, out_dir, fps=10)

        assert len(frames) == 2
        assert frames[0].name == FRAME_PATTERN % 1
        assert frames[1].name == FRAME_PATTERN % 2

    def test_creates_output_dir(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'new' / 'nested' / 'dir'

        def fake_popen(cmd, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / (FRAME_PATTERN % 1)).touch()
            return self._make_mock_process([])

        with patch('subprocess.Popen', side_effect=fake_popen):
            wrapper.extract_frames(video, out_dir, fps=5)

        assert out_dir.exists()

    def test_start_time_in_command(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / (FRAME_PATTERN % 1)).touch()
            return self._make_mock_process([])

        with patch('subprocess.Popen', side_effect=fake_popen):
            wrapper.extract_frames(video, out_dir, fps=10, start_time=5.0)

        assert '-ss' in captured_cmd
        ss_index = captured_cmd.index('-ss')
        assert captured_cmd[ss_index + 1] == '5.000'

    def test_duration_in_command(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / (FRAME_PATTERN % 1)).touch()
            return self._make_mock_process([])

        with patch('subprocess.Popen', side_effect=fake_popen):
            wrapper.extract_frames(video, out_dir, fps=10, duration=3.0)

        assert '-t' in captured_cmd
        t_index = captured_cmd.index('-t')
        assert captured_cmd[t_index + 1] == '3.000'

    def test_no_start_time_omits_ss_flag(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'
        captured_cmd = []

        def fake_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / (FRAME_PATTERN % 1)).touch()
            return self._make_mock_process([])

        with patch('subprocess.Popen', side_effect=fake_popen):
            wrapper.extract_frames(video, out_dir, fps=10)  # start_time defaults to 0.0

        assert '-ss' not in captured_cmd

    def test_progress_callback_called(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'
        progress_values = []

        def fake_popen(cmd, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / (FRAME_PATTERN % 1)).touch()
            return self._make_mock_process(
                [
                    'frame=  5 fps= 0 q=-1.0 size=N/A time=00:00:02.50 bitrate=N/A\n',
                    'frame= 10 fps= 0 q=-1.0 size=N/A time=00:00:05.00 bitrate=N/A\n',
                ]
            )

        with patch('subprocess.Popen', side_effect=fake_popen):
            wrapper.extract_frames(
                video, out_dir, fps=10, duration=5.0, progress_callback=progress_values.append
            )

        # Progress values + final 1.0
        assert 1.0 in progress_values
        assert all(0.0 <= p <= 1.0 for p in progress_values)

    def test_ffmpeg_nonzero_exit_raises(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'

        with patch('subprocess.Popen', return_value=self._make_mock_process([], returncode=1)):
            with pytest.raises(FFmpegError, match='exited with code 1'):
                wrapper.extract_frames(video, out_dir, fps=10)

    def test_no_frames_produced_raises(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'
        out_dir.mkdir()

        with patch('subprocess.Popen', return_value=self._make_mock_process([], returncode=0)):
            with pytest.raises(FFmpegError, match='No frames'):
                wrapper.extract_frames(video, out_dir, fps=10)

    def test_file_not_found_raises(self, wrapper, tmp_path):
        with pytest.raises(FileNotFoundError):
            wrapper.extract_frames(tmp_path / 'missing.mp4', tmp_path / 'out', fps=10)

    def test_frames_returned_sorted(self, wrapper, tmp_path):
        video = tmp_path / 'clip.mp4'
        video.touch()
        out_dir = tmp_path / 'frames'

        def fake_popen(cmd, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            # Create in reverse order to verify sorting
            for i in [3, 1, 2]:
                (out_dir / (FRAME_PATTERN % i)).touch()
            return self._make_mock_process([])

        with patch('subprocess.Popen', side_effect=fake_popen):
            frames = wrapper.extract_frames(video, out_dir, fps=10)

        assert [f.name for f in frames] == [FRAME_PATTERN % i for i in [1, 2, 3]]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
