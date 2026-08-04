# Plan 002: Stop dropping the final chunk of every video (chunker)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on.
> On any STOP condition, stop and report. When done, update your row in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- video-chunker/chunker.py`
> Note: `chunker.py` had uncommitted working-tree changes when this plan was
> written; excerpts reflect the working tree. On mismatch with the excerpts
> below, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW — additive fix at a single seam; covered by unit tests.
- **Depends on**: plans/001-verification-baseline.md
- **Category**: bug
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

Every strategy (`fixed`, `silence`, `scene`, `smart`) returns a list of chunk
**start times** that never includes the video's end. `process_single_video`
then creates chunks by pairing consecutive cut points, so the segment from the
last cut point to the end of the video — up to `target_duration + 20` seconds,
i.e. **the ending of every episode** — is silently never written to disk.

## Current state

- `video-chunker/chunker.py:425-432` — `find_cut_points_fixed`:

```python
def find_cut_points_fixed(self, duration):
    """Simple fixed-interval cutting (FASTEST)"""
    cut_points = []
    current = 0
    while current < duration:
        cut_points.append(current)
        current += self.target_duration
    return cut_points
```

For `duration=300`, `target_duration=120` this returns `[0, 120, 240]`.

- `video-chunker/chunker.py:434-463` — `find_cut_points_silence`: appends a cut only `if cut_point > current_time and cut_point < duration` (line 457), so `duration` is never in the list. Same pattern in `find_cut_points_scene` (line 497).
- `video-chunker/chunker.py:529-540` — `find_cut_points` dispatches to the strategy methods; it already computes `duration` via `self.get_video_info(video_path)` at line 531.
- `video-chunker/chunker.py:696-712` — consumer:

```python
for i in range(len(cut_points) - 1):
    start = cut_points[i]
    end = cut_points[i + 1]
```

So `[0, 120, 240]` yields chunks (0–120) and (120–240); 240–300 is lost.

## Commands you will need

| Purpose | Command (from `video-chunker/`) | Expected |
|---------|--------------------------------|----------|
| Syntax  | `python -m py_compile chunker.py` | exit 0 |
| Tests   | `python -m pytest tests/ -v` | all pass, **0 xfail** after this plan |

## Scope

**In scope**: `video-chunker/chunker.py` (only `find_cut_points`), `video-chunker/tests/test_chunker.py`, `plans/README.md`.

**Out of scope**: the strategy methods' internal search logic, `create_chunk`, anything in `local_video_scroller/`.

## Steps

### Step 1: Append the end-of-video cut point at the single dispatch seam

Fix in ONE place — `find_cut_points` (chunker.py:529-540) — rather than in each strategy. After obtaining the strategy's list, append `duration` if it isn't effectively there already, and drop degenerate tail chunks shorter than 3 seconds (a fixed-strategy video of exactly 240s currently produces `[0, 120]`; appending 240 is correct, but a 241s video must not produce a 1-second chunk — merge it into the previous one by replacing the last cut with `duration`):

```python
def find_cut_points(self, video_path):
    """Main entry point for finding cut points"""
    duration, _, _ = self.get_video_info(video_path)

    if self.strategy == "fixed":
        cut_points = self.find_cut_points_fixed(duration)
    elif self.strategy == "scene":
        cut_points = self.find_cut_points_scene(video_path, duration)
    elif self.strategy == "smart":
        cut_points = self.find_cut_points_smart(video_path, duration)
    else:
        cut_points = self.find_cut_points_silence(video_path, duration)

    # Ensure the tail of the video is included as the final chunk.
    MIN_TAIL = 3.0
    if not cut_points:
        cut_points = [0]
    if duration - cut_points[-1] >= MIN_TAIL:
        cut_points.append(duration)
    elif len(cut_points) >= 2:
        cut_points[-1] = duration  # absorb a tiny tail into the last chunk
    return cut_points
```

**Verify**: `python -m py_compile chunker.py` → exit 0.

### Step 2: Flip the xfail test and extend coverage

In `video-chunker/tests/test_chunker.py`:
- Remove the `@pytest.mark.xfail` from the tail-chunk test (added by plan 001); assert `find_cut_points` (the dispatcher — mock/monkeypatch `get_video_info` to return `(300, 1920, 1080)`) with `strategy="fixed"`, `target_duration=120` returns `[0, 120, 240, 300]`.
- Add: duration exactly divisible (240s, 120s target) → `[0, 120, 240]` and no 0-length tail.
- Add: 241s → last chunk absorbs the 1s tail → `[0, 120, 241]`.

Use `monkeypatch.setattr(chunker_instance, "get_video_info", lambda p: (300, 1920, 1080))` so no ffprobe is needed.

**Verify**: `python -m pytest tests/ -v` → all pass, no xfail remaining.

## Test plan

Covered by Step 2 (unit level, no ffmpeg required). Optional manual check if
ffmpeg and a sample video are available: run
`python chunker.py <dir-with-one-short-video> --strategy fixed -d 60 --vertical none --no-subtitles`
and confirm the sum of chunk durations ≈ source duration (`ffprobe -show_entries format=duration`).

## Done criteria

- [ ] `python -m pytest tests/ -v` exits 0 with zero xfail
- [ ] `find_cut_points` returns a list ending at (or absorbing into) `duration` for all strategies
- [ ] No changes outside in-scope files (`git status`)
- [ ] `plans/README.md` row updated

## STOP conditions

- `find_cut_points` (chunker.py:529) no longer matches the excerpt (someone already fixed or restructured dispatch).
- Tests from plan 001 are absent (`video-chunker/tests/` missing) — execute plan 001 first.

## Maintenance notes

- Any new chunking strategy must go through `find_cut_points` so the tail
  guarantee holds; reviewers should reject strategies called directly.
- Plan 008 (chunker performance) touches the same file but different
  functions; no conflict expected, but land this first.
