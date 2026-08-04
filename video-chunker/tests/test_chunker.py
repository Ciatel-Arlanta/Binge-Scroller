import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from chunker import VideoChunker


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
