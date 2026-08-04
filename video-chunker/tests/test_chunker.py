import sys
from pathlib import Path

import types

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from chunker import VideoChunker, _escape_subtitle_path


@pytest.fixture
def chunker(tmp_path):
    return VideoChunker(
        tmp_path,
        output_dir=tmp_path / "out",
        hw_accel="none",
        target_duration=120,
    )


class TestExtractShowInfo:
    def test_sxxexx_pattern(self, chunker):
        show, season, episode = chunker.extract_show_info(
            "Show.Name.S01E05.1080p.mkv"
        )
        assert (show, season, episode) == ("Show Name", "01", "05")

    def test_nxnn_pattern(self, chunker):
        show, season, episode = chunker.extract_show_info(
            "Show 2x07 something.mp4"
        )
        assert season == "02"
        assert episode == "07"
        assert show == "Show"

    def test_fallback_no_pattern(self, chunker):
        show, season, episode = chunker.extract_show_info("no_pattern_here.mp4")
        assert show == "no_pattern_here"
        assert season == "01"
        assert episode == "01"


class TestFindCutPointsFixed:
    def test_interval_spacing(self, chunker):
        cuts = chunker.find_cut_points_fixed(300)
        assert cuts == [0, 120, 240]


class TestFindCutPoints:
    """Dispatcher (find_cut_points) appends/absorbs the video end."""

    def test_final_chunk_boundary_included(self, chunker, monkeypatch):
        monkeypatch.setattr(
            chunker, "get_video_info", lambda p: (300, 1920, 1080)
        )
        chunker.strategy = "fixed"
        cuts = chunker.find_cut_points("dummy.mp4")
        assert cuts == [0, 120, 240, 300]

    def test_exact_duration_no_zero_length_tail(self, chunker, monkeypatch):
        monkeypatch.setattr(
            chunker, "get_video_info", lambda p: (240, 1920, 1080)
        )
        chunker.strategy = "fixed"
        cuts = chunker.find_cut_points("dummy.mp4")
        assert cuts == [0, 120, 240]
        # No degenerate final segment
        for i in range(len(cuts) - 1):
            assert cuts[i + 1] - cuts[i] > 0

    def test_tiny_tail_absorbed(self, chunker, monkeypatch):
        # 241s with 120s target: fixed yields [0, 120, 240]; 1s tail < MIN_TAIL
        # is absorbed into the last cut → [0, 120, 241]
        monkeypatch.setattr(
            chunker, "get_video_info", lambda p: (241, 1920, 1080)
        )
        chunker.strategy = "fixed"
        cuts = chunker.find_cut_points("dummy.mp4")
        assert cuts == [0, 120, 241]


class TestFindCutPointsSilence:
    def test_cuts_at_silence_midpoint(self, chunker):
        silence_periods = [(118, 122, 4.0)]
        cuts = chunker.find_cut_points_silence(
            video_path="dummy.mp4",
            duration=240,
            silence_periods=silence_periods,
        )
        assert len(cuts) >= 2
        assert cuts[1] == 120.0


class TestEscapeSubtitlePath:
    def test_windows_path(self):
        assert _escape_subtitle_path(r"C:\vids\a.mkv") == r"C\:/vids/a.mkv"

    def test_forward_slashes(self):
        assert _escape_subtitle_path("C:/vids/a.mkv") == r"C\:/vids/a.mkv"


class TestGetVerticalFilterSubtitles:
    """Filter strings for image vs text subtitle burn-in (no ffmpeg)."""

    @pytest.fixture
    def blur_chunker(self, tmp_path):
        return VideoChunker(
            tmp_path,
            output_dir=tmp_path / "out",
            hw_accel="none",
            target_duration=120,
            vertical_format="blur",
            output_resolution="720x1280",
        )

    def test_text_subs_use_subtitles_filter(self, blur_chunker):
        sub_info = {
            "sub_index": 0,
            "codec": "ass",
            "lang": "eng",
            "title": "Full",
        }
        filt = blur_chunker.get_vertical_filter(
            1920,
            1080,
            sub_info=sub_info,
            video_path=r"C:\vids\a.mkv",
        )
        assert filt is not None
        assert "subtitles=" in filt and "si=0" in filt and r"C\:/vids/a.mkv" in filt
        assert "[0:s:0]overlay" not in filt

    def test_pgs_subs_use_overlay(self, blur_chunker):
        sub_info = {
            "sub_index": 0,
            "codec": "hdmv_pgs_subtitle",
            "lang": "eng",
            "title": "PGS",
        }
        filt = blur_chunker.get_vertical_filter(
            1920,
            1080,
            sub_info=sub_info,
            video_path=r"C:\vids\a.mkv",
        )
        assert filt is not None
        assert "[0:s:0]overlay" in filt
        assert "subtitles=" not in filt

    def test_no_subs_unchanged(self, blur_chunker):
        filt = blur_chunker.get_vertical_filter(1920, 1080, sub_info=None)
        assert filt is not None
        assert "subtitles=" not in filt
        assert "v_with_subs" not in filt


class TestCreateChunkRobustness:
    """create_chunk: copy mapping, resume skip, ffmpeg error surfacing."""

    @pytest.fixture
    def copy_chunker(self, tmp_path):
        return VideoChunker(
            tmp_path,
            output_dir=tmp_path / "out",
            hw_accel="none",
            target_duration=120,
            vertical_format="none",
            re_encode=False,
            burn_subtitles=False,
        )

    def test_copy_path_maps_video_and_audio(self, copy_chunker, tmp_path, monkeypatch):
        recorded = {}

        def fake_run(cmd, **kwargs):
            recorded["cmd"] = list(cmd)
            # Mimic ffmpeg writing the output path (last arg)
            Path(cmd[-1]).write_bytes(b"ok")
            return types.SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr("chunker.subprocess.run", fake_run)
        out = tmp_path / "out" / "chunk.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        name = copy_chunker.create_chunk(
            video_path=tmp_path / "in.mkv",
            start=0,
            end=10,
            output_path=out,
            input_width=1920,
            input_height=1080,
            sub_info=None,
        )
        cmd = recorded["cmd"]
        # map flags present as consecutive pairs
        assert "-map" in cmd
        map_args = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-map"]
        assert "0:v:0" in map_args
        assert "0:a:0?" in map_args
        assert "-c" in cmd and "copy" in cmd
        assert name == "chunk.mp4"
        assert out.exists()  # renamed from tmp

    def test_resume_skips_existing(self, copy_chunker, tmp_path, monkeypatch):
        called = {"n": 0}

        def fake_run(cmd, **kwargs):
            called["n"] += 1
            return types.SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr("chunker.subprocess.run", fake_run)
        out = tmp_path / "out" / "existing.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"already-rendered-content")
        name = copy_chunker.create_chunk(
            video_path=tmp_path / "in.mkv",
            start=0,
            end=10,
            output_path=out,
            input_width=1920,
            input_height=1080,
            sub_info=None,
        )
        assert name == "existing.mp4"
        assert called["n"] == 0

    def test_ffmpeg_failure_surfaces_stderr(self, copy_chunker, tmp_path, monkeypatch):
        def fake_run(cmd, **kwargs):
            return types.SimpleNamespace(
                returncode=1, stderr="line1\nBAD THING\nline3"
            )

        monkeypatch.setattr("chunker.subprocess.run", fake_run)
        out = tmp_path / "out" / "fail.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        with pytest.raises(RuntimeError) as ei:
            copy_chunker.create_chunk(
                video_path=tmp_path / "in.mkv",
                start=0,
                end=10,
                output_path=out,
                input_width=1920,
                input_height=1080,
                sub_info=None,
            )
        assert "BAD THING" in str(ei.value)
        assert not out.exists()
        assert not out.with_suffix(".tmp.mp4").exists()
