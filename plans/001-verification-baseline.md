# Plan 001: Establish a working verification baseline (fix broken test, add unit tests)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- local_video_scroller/test local_video_scroller/lib/models video-chunker/`
> Note: `video-chunker/chunker.py` had uncommitted working-tree changes when this
> plan was written; the excerpts below reflect the **working tree**, not the commit.
> If excerpts don't match the live code, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW — only adds/replaces tests, no product code changes.
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

There is currently **no command that passes** to verify this project works.
`local_video_scroller/test/widget_test.dart` is the stock Flutter counter
test — it pumps `MyApp` and expects a counter and an `Icons.add` button that
don't exist in this app, so `flutter test` fails. The Python chunker has no
tests at all. Every other plan in `plans/` uses `flutter test` /
`pytest` as verification gates, so this plan must land first.

## Current state

- `local_video_scroller/test/widget_test.dart:15-30` — "Counter increments smoke test": expects `find.text('0')`, taps `Icons.add`. The real app (`lib/main.dart`) is a video feed with no counter. Additionally `MyApp` is pumped without the `ChangeNotifierProvider<VideoStateProvider>` that `main()` normally supplies, so the test would crash even if assertions matched.
- `local_video_scroller/lib/models/video_model.dart:17-40` — `VideoModel.fromPath` parses filenames like `Show_Name_S01E02_Part003.mp4` with regex `(.+?)_S(\d+)E(\d+)_Part(\d+)\.mp4`, with a fallback (season=1, episode=1, part=1) for non-matching names. Pure logic, ideal unit-test target. Note it splits on `'/'` (line 18), so pass POSIX-style paths in tests.
- `video-chunker/chunker.py:96-113` — `extract_show_info(filename)` parses `S01E01`, `1x01`, `Season1Episode1` patterns; fallback returns `(stem, "01", "01")`. Pure logic.
- `video-chunker/chunker.py:425-432` — `find_cut_points_fixed(duration)` returns start times `[0, d, 2d, ...]`. **Known bug (fixed by plan 002): the list of cut points never includes the final `duration`, so the last partial chunk is dropped.** Write the test to assert current behavior only for the interval spacing, NOT for the tail — or mark the tail-chunk test `xfail` referencing plan 002.
- There is no `pytest` config and no `video-chunker/tests/` directory.
- `VideoChunker.__init__` runs `_detect_hw_encoder()` (spawns ffmpeg) and `mkdir`. For unit tests, construct with `hw_accel="none"` and `output_dir` pointing at `tmp_path` to avoid side effects, e.g. `VideoChunker(tmp_path, output_dir=tmp_path / "out", hw_accel="none")`.

## Commands you will need

| Purpose | Command (run from the listed dir) | Expected on success |
|---------|-----------------------------------|---------------------|
| Flutter analyze | `local_video_scroller>` `flutter analyze` | exit 0 (info-level lints acceptable; no errors) |
| Flutter tests | `local_video_scroller>` `flutter test` | all pass |
| Python syntax | `video-chunker>` `python -m py_compile chunker.py` | exit 0, no output |
| Python tests | `video-chunker>` `python -m pytest tests/ -v` | all pass |

If `pytest` is not installed: `pip install pytest` (it is a dev-only dependency; also create `video-chunker/requirements-dev.txt` containing `pytest` and `tqdm`).

## Scope

**In scope** (only files you may create/modify):
- `local_video_scroller/test/widget_test.dart` (replace)
- `local_video_scroller/test/video_model_test.dart` (create)
- `video-chunker/tests/test_chunker.py` (create, plus empty `tests/__init__.py` if needed)
- `video-chunker/requirements-dev.txt` (create)
- `plans/README.md` (status row)

**Out of scope**: any file under `lib/` or `chunker.py` itself — this plan adds tests only. If a test reveals a bug, assert the *current* behavior with a comment naming the follow-up plan, or use `xfail`.

## Steps

### Step 1: Replace the stale widget test

Delete the counter test in `local_video_scroller/test/widget_test.dart`. Replace with a minimal smoke test that does NOT pump `VideoFeedScreen` (it requires platform channels for permissions/storage that don't exist in the test harness). Test `MaterialApp` construction only, e.g. pump `const MaterialApp(home: Scaffold(body: Text('smoke')))` and assert the text exists — its purpose is solely to keep `flutter test` green as a harness check. Real widget coverage is deferred (see Maintenance notes).

**Verify**: `flutter test test/widget_test.dart` → 1 test passes.

### Step 2: Add VideoModel unit tests

Create `local_video_scroller/test/video_model_test.dart` using `package:flutter_test/flutter_test.dart` (plain `group`/`test`, no widgets). Cases:

1. `VideoModel.fromPath('/x/Attack_on_Titan_S01E02_Part003.mp4')` → showName `Attack on Titan`, season 1, episode 2, part 3, displayName `Attack on Titan S01E02 Part003`.
2. Fallback: `VideoModel.fromPath('/x/random_clip.mp4')` → showName `random_clip`, season 1, episode 1, part 1.
3. Multi-digit: `..._S12E34_Part120.mp4` parses as 12/34/120.
4. `toJson`/`fromJson` round-trip preserves all fields.

**Verify**: `flutter test` → all tests pass (Step 1 + Step 2 tests).

### Step 3: Add chunker unit tests

Create `video-chunker/tests/test_chunker.py`. Import with `sys.path.insert(0, str(Path(__file__).parent.parent))` then `from chunker import VideoChunker` (chunker.py is a flat script, not a package). Build the instance in a fixture using `tmp_path` and `hw_accel="none"` as noted in Current state. Cases:

1. `extract_show_info("Show.Name.S01E05.1080p.mkv")` → `("Show Name", "01", "05")`.
2. `extract_show_info("Show 2x07 something.mp4")` → season `02`, episode `07`.
3. `extract_show_info("no_pattern_here.mp4")` → stem fallback, `"01"`, `"01"`.
4. `find_cut_points_fixed(300)` with `target_duration=120` → starts with `[0, 120, 240]`. Add a second test asserting the final chunk boundary (`duration` present as last cut point) marked `@pytest.mark.xfail(reason="plan 002: final chunk dropped")`.
5. `find_cut_points_silence` with an injected `silence_periods` list (pass via the `silence_periods=` parameter, no ffmpeg needed): with `target_duration=120` and a silence at `(118, 122, 4.0)` over `duration=240`, assert the second cut point is `120.0` (midpoint of the silence).

**Verify**: `python -m pytest tests/ -v` → all pass, 1 xfail.

### Step 4: Create requirements-dev.txt

`video-chunker/requirements-dev.txt` with two lines: `tqdm` and `pytest`.

**Verify**: `pip install -r requirements-dev.txt` → exit 0.

## Done criteria

- [ ] `flutter test` exits 0 in `local_video_scroller/`
- [ ] `flutter analyze` reports no errors (infos/warnings acceptable)
- [ ] `python -m pytest tests/ -v` in `video-chunker/` exits 0 (xfail allowed)
- [ ] `grep -n "Counter increments" local_video_scroller/test/widget_test.dart` returns nothing
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- `flutter test` fails for infrastructure reasons unrelated to the test content (SDK/toolchain errors) — report the toolchain error instead of patching around it.
- `from chunker import VideoChunker` triggers ffmpeg detection despite `hw_accel="none"` (constructor behavior changed) — report; do not refactor `chunker.py`.
- The regexes in `video_model.dart` or `chunker.py` differ from the excerpts above.

## Maintenance notes

- Plans 002–008 rely on these commands as gates; keep them green.
- Widget-level tests of `VideoPlayerWidget` need a fake `VideoPlayerController` platform; deferred deliberately — revisit after plan 004 (player hardening).
- The xfail in Step 3 must flip to a passing assertion when plan 002 lands.
