# Plan 007: Chunker robustness — visible errors, safe stream-copy, resume, no bare excepts

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. When done, update your row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- video-chunker/chunker.py`
> `chunker.py` had uncommitted working-tree changes at planning time; excerpts
> reflect the working tree. Drift from plans 002/006 is expected; other
> mismatches in the excerpted regions are a STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW–MED — mostly additive; the stream-copy mapping change alters ffmpeg args on the copy path.
- **Depends on**: plans/001-verification-baseline.md (tests exist); recommended after 002.
- **Category**: bug / tech-debt
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

Four related weaknesses make failures silent or expensive:

1. **All ffmpeg errors are invisible.** `create_chunk` runs with
   `stderr=DEVNULL` + `check=True`; a failure surfaces as
   `Error processing chunk: Command '...' returned non-zero exit status 1`
   with zero diagnostic content.
2. **The stream-copy fast path can fail or bloat output.** With
   `--vertical none --no-subtitles` and no `--re-encode`, `-c copy` copies
   *all* streams from an MKV into an `.mp4` container — PGS subtitle or
   attachment streams make the mp4 muxer error out; extra audio tracks bloat
   files the app plays with track 0 anyway.
3. **No resume.** Re-running after an interruption re-encodes every chunk.
4. **Bare `except:` clauses** in the ffmpeg-output parsers swallow
   `KeyboardInterrupt` and hide parse bugs.

## Current state

All in `video-chunker/chunker.py`:

- Lines 641-651, end of `create_chunk` (copy path + execution):

```python
else:
    # Fast stream copy
    cmd.extend([
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart"
    ])

cmd.append(str(output_path))

subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

- Lines 714-721, `process_single_video` error consumer: `print(f"\nError processing chunk: {e}")`.
- Bare `except:` sites: lines 332-333 (`detect_scene_changes`), 356-357 / 365-367 (`detect_silence_fast`), 411-412 / 420-421 (`detect_silence_windowed`).
- Chunk naming (lines 698-705): `f"{show_name}_S{season}E{episode}_Part{part_str}.mp4"` into `self.output_dir` — deterministic, which makes skip-if-exists safe.
- `get_video_info` (lines 194-210): `info['streams'][0]['width']` raises bare `KeyError/IndexError` on an audio-only or corrupt file, crashing the whole batch (`process_videos` loops files with no per-file try, lines 744-745).

## Commands you will need

| Purpose | Command (from `video-chunker/`) | Expected |
|---------|--------------------------------|----------|
| Syntax  | `python -m py_compile chunker.py` | exit 0 |
| Unit    | `python -m pytest tests/ -v` | pass |
| Manual  | run on a scratch folder (asset recipe in plan 006 Step 3) | chunks produced; re-run skips them |

## Scope

**In scope**: `video-chunker/chunker.py`, `video-chunker/tests/test_chunker.py`, `plans/README.md`.

**Out of scope**: encoder quality settings, filter graphs (plan 006), detection algorithms' logic (plan 008), the Flutter app.

## Steps

### Step 1: Capture and surface ffmpeg stderr

In `create_chunk`, replace the final `subprocess.run` with:

```python
result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE, text=True)
if result.returncode != 0:
    tail = "\n".join(result.stderr.strip().splitlines()[-8:])
    raise RuntimeError(
        f"ffmpeg failed for {output_path.name} (exit {result.returncode}):\n{tail}"
    )
```

In `process_single_video`'s `except` (line 720-721), keep the print — it now
carries the stderr tail.

**Verify**: `python -m py_compile chunker.py` → exit 0. Manual: temporarily point at a folder containing a zero-byte `broken.mp4` — the error printed includes actual ffmpeg text (then delete the file).

### Step 2: Map only video+audio on the copy path

In the copy branch, add explicit mapping before `-c copy`:

```python
cmd.extend([
    "-map", "0:v:0",
    "-map", "0:a:0?",          # '?' = optional, tolerate silent clips
    "-c", "copy",
    "-avoid_negative_ts", "make_zero",
    "-movflags", "+faststart"
])
```

**Verify**: unit test (Step 5) asserts the built command; manual run on the plan-006 sample MKV with `--vertical none --no-subtitles` → chunks are produced (previously this could fail on the srt stream) and `ffprobe -show_entries stream=codec_type <chunk>` lists exactly one video and one audio stream.

### Step 3: Skip already-rendered chunks (resume)

At the top of `create_chunk`, before building the command:

```python
if output_path.exists() and output_path.stat().st_size > 0:
    return output_path.name  # already rendered (resume support)
```

Since interrupted runs can leave a truncated file, write to a temp name and
rename on success: output to `output_path.with_suffix(".tmp.mp4")`, and after
the successful run `os.replace(tmp_path, output_path)`. Update the failure
branch to delete the tmp file if it exists.

**Verify**: manual — run the chunker twice on the same scratch folder; second run finishes near-instantly and prints no encoding progress for existing chunks.

### Step 4: Replace bare excepts and harden `get_video_info` / per-file loop

- Change every bare `except:` at the listed sites to `except (ValueError, IndexError):`.
- In `get_video_info`, wrap the dict access: on `KeyError/IndexError/ValueError/json.JSONDecodeError`, raise `RuntimeError(f"Could not read video info from {video_path.name}")`.
- In `process_videos` (lines 744-745), wrap `self.process_single_video(video_file)` in try/except that prints the error and continues with the next file; track and print a final count of failed files.

**Verify**: `python -m py_compile chunker.py` → exit 0; batch with one corrupt file completes the healthy files and reports 1 failure.

### Step 5: Unit tests

Add to `tests/test_chunker.py` (instance with `hw_accel="none"`, monkeypatch
`subprocess.run` to record the command and return a fake success —
`types.SimpleNamespace(returncode=0, stderr="")`):

1. Copy path (`vertical_format="none"`, `sub_info=None`, `re_encode=False`): recorded cmd contains `["-map", "0:v:0", "-map", "0:a:0?"]` and `["-c", "copy"]`.
2. Resume: pre-create `output_path` with content in `tmp_path` → `create_chunk` returns without calling `subprocess.run`.
3. Failure surfacing: fake `returncode=1, stderr="line1\nBAD THING"` → `RuntimeError` raised whose message contains `BAD THING`.

**Verify**: `python -m pytest tests/ -v` → all pass.

## Done criteria

- [ ] `python -m pytest tests/ -v` exits 0 (new tests included)
- [ ] `grep -n "except:" chunker.py` → no bare excepts remain
- [ ] `grep -n "stderr=subprocess.DEVNULL" chunker.py` → not present in `create_chunk`
- [ ] Re-running on an already-processed folder skips all chunks
- [ ] No files outside scope modified

## STOP conditions

- The copy branch or `create_chunk` tail no longer matches the excerpt.
- After Step 2, the manual copy-path run produces chunks with no audio (the `0:a:0?` mapping hit an unexpected layout) — report the source file's `ffprobe` stream list.

## Maintenance notes

- The temp-file+rename pattern in Step 3 is what makes resume safe; any new
  output-writing path must follow it.
- If chunk filename format ever changes, resume comparisons silently miss old
  outputs — acceptable, but note it in the changelog.
