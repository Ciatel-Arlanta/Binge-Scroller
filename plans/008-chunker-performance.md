# Plan 008: Chunker performance — parallel silence windows, cheap scene detection, single probe

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. When done, update your row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- video-chunker/chunker.py`
> `chunker.py` had uncommitted working-tree changes at planning time; excerpts
> reflect the working tree. Drift from plans 002/006/007 is expected; other
> mismatches in the excerpted regions are a STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED — changes analysis behavior; must show equal-or-similar cut points on a sample, not just "runs faster".
- **Depends on**: plans/002-fix-final-chunk-dropped.md (same file; its tests protect cut-point behavior)
- **Category**: perf
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

For a 40-minute episode with the default `smart` strategy, analysis time is
dominated by two full-decode passes and serialized window scans:

1. **Scene detection decodes the video at full resolution with audio** —
   for 1080p content this is often slower than the encoding itself.
2. **Windowed silence detection runs its ~20 windows sequentially**, one
   ffmpeg process at a time, even though each window is independent.
3. **`ffprobe` runs twice per file** — `process_single_video` (line 665) and
   `find_cut_points` (line 531) both call `get_video_info`.

Combined, these typically cut the analysis phase by 3–5x with identical or
near-identical output.

## Current state

All in `video-chunker/chunker.py`:

- Lines 313-335, `detect_scene_changes`:

```python
cmd = [
    "ffmpeg", "-i", str(video_path),
    "-filter:v", f"select='gt(scene,{threshold})',showinfo",
    "-f", "null", "-"
]
```

No downscale, no `-an` — full-res decode plus audio decode.

- Lines 371-423, `detect_silence_windowed`: `for cut_time in tqdm(expected_cuts, ...)` spawns one ffmpeg per window, sequentially; per-window results are offset by `window_start` and appended to `silence_periods`.
- Lines 529-540 `find_cut_points` calls `get_video_info(video_path)`; lines 664-665 `process_single_video` also calls it. Two ffprobe spawns per file.
- `concurrent.futures` is already imported (line 6); `ThreadPoolExecutor` is already used for chunk creation (line 697).

## Commands you will need

| Purpose | Command (from `video-chunker/`) | Expected |
|---------|--------------------------------|----------|
| Syntax  | `python -m py_compile chunker.py` | exit 0 |
| Unit    | `python -m pytest tests/ -v` | pass |
| Manual timing | `python -c "import time,..."` or just time two runs on the plan-006 sample extended to ~5 min (`testsrc=d=300`) | analysis phase measurably faster, same chunk count |

## Scope

**In scope**: `video-chunker/chunker.py` (`detect_scene_changes`, `detect_silence_windowed`, `find_cut_points`, `process_single_video` signature plumbing only), `video-chunker/tests/test_chunker.py`, `plans/README.md`.

**Out of scope**: `create_chunk` and encoder settings (plan 007 owns error handling there), cut-point *selection* logic, adding dependencies.

## Steps

### Step 1: Cheap scene detection

In `detect_scene_changes`, change the filter to downscale before scoring and
skip audio:

```python
cmd = [
    "ffmpeg", "-i", str(video_path),
    "-an", "-sn",
    "-filter:v", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
    "-f", "null", "-"
]
```

Scene scores are computed on frame-to-frame difference; downscaling changes
absolute scores slightly but rank order of strong scene cuts survives. Keep
the threshold parameter as is.

**Verify**: on the 5-minute synthetic sample, `--strategy scene` produces a similar cut count (±20%) to the pre-change run, and wall-clock time for the detection print (`Detecting scenes...` to the next print) drops. Record both timings in the completion report.

### Step 2: Parallelize silence windows

In `detect_silence_windowed`, extract the per-window work (build cmd, run,
parse, offset by `window_start`) into an inner function
`_scan_window(cut_time)` returning a list of periods. Run windows with
`concurrent.futures.ThreadPoolExecutor(max_workers=min(self.max_workers, 8))`
and flatten results, keeping tqdm over `as_completed`. Sort the merged
`silence_periods` by start time before returning (the sequential version was
implicitly ordered; `find_cut_points_silence` iterates all periods so order
only matters for determinism — sort anyway).

**Verify**: `python -m pytest tests/ -v` still passes; manual run of `--strategy silence` on the sample produces the same number of chunks as before the change.

### Step 3: Probe once per file

Change `find_cut_points(self, video_path)` to accept the duration:
`find_cut_points(self, video_path, duration)`, delete its internal
`get_video_info` call, and in `process_single_video` reuse the values from the
existing call at line 664-665: `duration, input_width, input_height = self.get_video_info(video_file)` then `cut_points = self.find_cut_points(video_file, duration)`.
Update any tests that call `find_cut_points` (plan 002 added ones that
monkeypatch `get_video_info` — simplify them to pass `duration` directly).

**Verify**: `python -m pytest tests/ -v` → pass; `grep -c "get_video_info" chunker.py` shows one fewer call site.

## Test plan

- Unit: existing cut-point tests keep passing (they pin behavior).
- Add one unit test for `detect_silence_windowed` parallel merge: monkeypatch
  `subprocess.run` to return canned silencedetect stderr
  (`"[silencedetect] silence_start: 5.0\n[silencedetect] silence_end: 6.0 | silence_duration: 1.0"`)
  and assert periods come back offset by each window's start and sorted.
- Manual A/B timing on the synthetic 5-minute file, reported.

## Done criteria

- [ ] `python -m pytest tests/ -v` exits 0
- [ ] `detect_scene_changes` command contains `scale=320:-2` and `-an`
- [ ] `detect_silence_windowed` uses a ThreadPoolExecutor
- [ ] One `get_video_info` call per processed file
- [ ] A/B timing recorded in the completion report; chunk counts unchanged on the sample
- [ ] No files outside scope modified

## STOP conditions

- The excerpted functions have been restructured beyond plans 002/006/007.
- Step 1 changes the sample's chunk count by more than ±20% — the threshold
  interacts with downscaling worse than expected; report the numbers instead
  of tuning the threshold yourself.
- Parallel window scans on your machine cause ffmpeg failures (resource
  exhaustion) — reduce the executor cap to 4 and note it; if still failing, STOP.

## Maintenance notes

- Anyone changing `scene` threshold defaults must re-run the A/B chunk-count
  comparison — the downscale makes absolute thresholds slightly hotter.
- Further speedup candidates deliberately deferred: keyframe-aligned copy
  cuts (`-ss` snapping), NVDEC-accelerated scene detection, and caching
  analysis results next to the source file. Revisit only if analysis is still
  the bottleneck after this plan.
