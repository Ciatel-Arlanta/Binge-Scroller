# Plan 006: Burn in text-based subtitles correctly (chunker)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. When done, update your row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- video-chunker/chunker.py`
> `chunker.py` had uncommitted working-tree changes at planning time; excerpts
> reflect the working tree. Plans 002/007/008 touch other functions in this
> file — that drift is expected. Mismatches inside `get_vertical_filter` or
> `create_chunk` are a STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED — ffmpeg filter syntax is finicky; requires a sample MKV with text subs to verify.
- **Depends on**: plans/001-verification-baseline.md
- **Category**: bug
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

Subtitle burn-in defaults to ON (`--subtitles`), and most anime/TV MKVs carry
**text** subtitles (ASS/SSA/SRT). The filter builder routes text subtitles
through the `overlay` filter, which only accepts *video* inputs — ffmpeg
errors out and, because stderr is discarded (see plan 007), every chunk fails
with an opaque "Error processing chunk". In practice subtitle burn-in only
works for image-based subs (PGS/DVD), which the code comment itself admits.

## Current state

`video-chunker/chunker.py:225-237` in `get_vertical_filter`:

```python
if sub_info is not None:
    si = sub_info["sub_index"]
    codec = sub_info["codec"]

    if "pgs" in codec or "dvd_sub" in codec or "dvb" in codec:
        # Image-based subtitles (PGS, DVD, DVB): use overlay
        sub_prefix = f"[0:v][0:s:{si}]overlay[v_with_subs];"
    else:
        # Text-based subtitles (ASS, SRT, etc): use subtitles filter
        # This path is less common for anime MKV but supported
        sub_prefix = f"[0:v][0:s:{si}]overlay[v_with_subs];"   # <-- same as image path: WRONG
    src_tag = "[v_with_subs]"
```

- Text subs need the `subtitles` filter, which takes the **filename**, not a
  stream label: `subtitles='path':si=N` (`si` = index among subtitle streams —
  the same indexing `detect_subtitle_stream` already returns at chunker.py:115-192).
- `create_chunk` (chunker.py:542-652) uses `-ss <start> -copyts` before `-i`
  when subs are active, and repeats `-ss <start> -t <dur>` on the output side
  (lines 550-556, 630-632) — this exists precisely to keep subtitle timestamps
  aligned; the `subtitles` filter honors source timestamps, so this structure
  can stay.
- Windows path caveat: the `subtitles` filter argument needs escaping — on
  Windows, `C:\dir\file.mkv` must become `C\:/dir/file.mkv` (escape the drive
  colon, use forward slashes) and the whole value wrapped in single quotes
  inside the filter string.
- `detect_subtitle_stream` returns `{"sub_index", "codec", "lang", "title"}`.

## Commands you will need

| Purpose | Command (from `video-chunker/`) | Expected |
|---------|--------------------------------|----------|
| Syntax  | `python -m py_compile chunker.py` | exit 0 |
| Unit    | `python -m pytest tests/ -v` | pass |
| Manual  | run against a short MKV with ASS/SRT subs (create one: see Test plan) | chunks play with visible subs |

## Scope

**In scope**: `video-chunker/chunker.py` (`get_vertical_filter` and, only if needed for passing the source path, its call sites in `create_chunk`), `video-chunker/tests/test_chunker.py`, `plans/README.md`.

**Out of scope**: `detect_subtitle_stream` scoring logic, encoder settings, the Flutter app.

## Steps

### Step 1: Add a path-escaping helper and branch the filter correctly

Add a module-level helper:

```python
def _escape_subtitle_path(path):
    """Escape a filesystem path for use inside an ffmpeg subtitles filter."""
    p = str(path).replace("\\", "/")
    p = p.replace(":", "\\:").replace("'", "\\'")
    return p
```

In `get_vertical_filter`, the function currently doesn't receive the video
path — change its signature to
`get_vertical_filter(self, input_width, input_height, sub_info=None, video_path=None)`
and update the single call site in `create_chunk` (line 569) to pass
`video_path=video_path`. Then:

```python
if "pgs" in codec or "dvd_sub" in codec or "dvb" in codec:
    sub_prefix = f"[0:v][0:s:{si}]overlay[v_with_subs];"
else:
    esc = _escape_subtitle_path(video_path)
    sub_prefix = f"[0:v]subtitles='{esc}':si={si}[v_with_subs];"
```

**Verify**: `python -m py_compile chunker.py` → exit 0.

### Step 2: Unit-test the filter strings

In `tests/test_chunker.py` add tests calling `get_vertical_filter` directly
(no ffmpeg needed), instance built with `hw_accel="none"`:

1. `sub_info={"sub_index":0,"codec":"ass",...}`, `video_path="C:\\vids\\a.mkv"`, `vertical_format="blur"` → returned string contains `subtitles='C\:/vids/a.mkv':si=0` and does NOT contain `[0:s:0]overlay`.
2. `codec="hdmv_pgs_subtitle"` → string contains `[0:s:0]overlay` and not `subtitles=`.
3. `sub_info=None` → string unchanged from pre-plan behavior (no `subtitles`, no `v_with_subs`).

**Verify**: `python -m pytest tests/ -v` → all pass.

### Step 3: End-to-end check with a generated sample

Create a disposable test asset (no copyrighted content needed):

```bash
ffmpeg -f lavfi -i testsrc=d=30:s=640x360 -f lavfi -i sine=d=30 -pix_fmt yuv420p base.mp4
printf '1\n00:00:01,000 --> 00:00:20,000\nHELLO SUBS\n' > subs.srt
ffmpeg -i base.mp4 -i subs.srt -c copy -c:s srt Sample_S01E01.mkv
```

Put `Sample_S01E01.mkv` alone in a scratch folder and run:
`python chunker.py <scratch> -d 15 --strategy fixed --vertical blur --resolution 720x1280 --hw-accel none -w 1`

**Verify**: output chunks exist and the text "HELLO SUBS" is visibly rendered in the first chunk (open it in any player, or extract a frame: `ffmpeg -ss 5 -i <chunk> -frames:v 1 frame.png`).

## Test plan

Steps 2 (unit) and 3 (E2E) above. If ffmpeg is not on PATH in your
environment, complete steps 1–2 and mark the row `BLOCKED (E2E pending: no ffmpeg)`.

## Done criteria

- [ ] `python -m pytest tests/ -v` exits 0 with the 3 new tests
- [ ] `grep -n "less common for anime" chunker.py` → no match (stale comment removed)
- [ ] E2E sample chunk shows burned text (or row BLOCKED per Test plan)
- [ ] No files outside scope modified

## STOP conditions

- `get_vertical_filter` already receives the video path or the text branch no longer duplicates the overlay call (someone fixed it).
- The E2E run fails inside ffmpeg with a filter-parse error after two escaping attempts — capture the exact ffmpeg stderr (temporarily remove the `stderr=DEVNULL` in `create_chunk`) and report it verbatim.

## Maintenance notes

- The `subtitles` filter re-opens the source file per chunk; with many
  parallel workers this adds I/O — the existing worker cap for subtitle jobs
  (chunker.py:691-693) should stay.
- If MKV chapters or external `.srt` sidecar support is added later, the
  escaping helper is the shared piece to reuse.
