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

    @pytest.mark.xfail(reason="plan 002: final chunk dropped")
    def test_final_chunk_boundary_included(self, chunker):
        # Known bug: cut points never include duration, so the last partial
        # chunk is dropped. Plan 002 should make duration the final cut point.
        cuts = chunker.find_cut_points_fixed(300)
        assert cuts[-1] == 300


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
