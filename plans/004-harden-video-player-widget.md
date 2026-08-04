# Plan 004: Harden VideoPlayerWidget — init/dispose race, progress-bar math, stale overlays

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. When done, update your row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- local_video_scroller/lib/widgets/video_player_widget.dart`
> If plan 003 has landed, its `_hasEnded` latch and `_onControllerUpdate`
> method will be present — that is expected drift, not a STOP. Any OTHER
> mismatch with the excerpts below is a STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED — restructures controller lifecycle; fast-scrolling is the main regression surface.
- **Depends on**: plans/003-fix-background-playback-and-video-end.md (same file; land 003 first to avoid conflicts)
- **Category**: bug
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

Three defects in `lib/widgets/video_player_widget.dart`:

1. **Crash on fast scroll (init/dispose race).** `_initializeVideo` awaits
   `initialize()`; if the widget is disposed (or the video path changes)
   during that await, `_disposeController` sets `_controller = null`, and the
   next line `_controller!.setVolume(...)` throws a null-check error. With
   `preloadPagesCount: 2` and quick flings this is reachable in normal use.
2. **Progress bar divides by zero / uses whole seconds.**
   `value.position.inSeconds / value.duration.inSeconds` is NaN when duration
   rounds to 0 (chunks < 1s, or pre-init) and makes the bar jump once per
   second instead of moving smoothly.
3. **The paused-title overlay uses `isPlaying` read outside any listener**, so
   it appears/disappears only when something else happens to rebuild.

## Current state

Excerpts from `local_video_scroller/lib/widgets/video_player_widget.dart` (line numbers at commit `8494213`; plan 003 shifts them slightly):

- Lines 66-103, `_initializeVideo` (race sites marked):

```dart
_controller = VideoPlayerController.file(
  File(widget.video.path),
  videoPlayerOptions: VideoPlayerOptions(mixWithOthers: true),
);
await _controller!.initialize();     // <- dispose can happen during this await
await _controller!.setVolume(1.0);   // <- throws if _controller was nulled
```

- Lines 105-114, `_disposeController` — synchronous: `pause()` (unawaited), `dispose()`, `_controller = null`.
- Lines 37-50, `didUpdateWidget` — on path change calls `_disposeController(); _initializeVideo();`.
- Lines 338-352, progress bar:

```dart
return LinearProgressIndicator(
  value: value.position.inSeconds / value.duration.inSeconds,
  ...
```

- Lines 295-316, title overlay gated by `if (!_controller!.value.isPlaying)` directly in `build`.
- Lines 138-178, `_seekVideo` — contains dead null checks: `currentPosition != null && duration != null` on non-nullable `Duration`s (analyzer flags these).

## Commands you will need

| Purpose | Command (from `local_video_scroller/`) | Expected |
|---------|----------------------------------------|----------|
| Analyze | `flutter analyze` | no errors; the dead-null-check infos disappear |
| Tests   | `flutter test`    | all pass |
| Manual  | `flutter run` + fast fling through ≥10 videos | no crash, no "Error loading video" flicker |

## Scope

**In scope**: `lib/widgets/video_player_widget.dart`, `plans/README.md`.

**Out of scope**: `video_feed_screen.dart` (including `preloadPagesCount`), provider/service/model files, adding new packages.

## Steps

### Step 1: Make initialization cancellation-safe

In `_initializeVideo`:
- Capture the controller into a local: `final controller = VideoPlayerController.file(...); _controller = controller;`
- After every `await`, check `if (!mounted || _isDisposed || _controller != controller) { await controller.dispose(); return; }` — i.e., if this init lost the race, dispose the *local* controller and bail; never touch state.
- Use the local `controller` (not `_controller!`) for `initialize`, `setVolume`, `addListener`, and the post-init `play()`.

In `didUpdateWidget`'s path-change branch, keep the current order
(`_disposeController(); _initializeVideo();`) — the stale-controller guard
above makes it safe.

In `_disposeController`, also reset `_hasEnded = false` if plan 003's latch
exists there.

**Verify**: `flutter analyze` → no errors. Manual: fling rapidly through the feed for ~30 seconds → no crash in `flutter logs` (look for `Null check operator used on a null value`).

### Step 2: Fix progress-bar math

Replace the `LinearProgressIndicator` value computation with a
millisecond-based, clamped expression:

```dart
final durMs = value.duration.inMilliseconds;
final progress = durMs > 0
    ? (value.position.inMilliseconds / durMs).clamp(0.0, 1.0)
    : 0.0;
```

and pass `progress` as `value:`.

**Verify**: `flutter analyze` → no errors. Manual: bar advances smoothly (not in 1-second steps).

### Step 3: Make the paused-title overlay reactive

Wrap the title overlay (currently `if (!_controller!.value.isPlaying) Positioned(...)`) in the same pattern the progress bar already uses — a `ValueListenableBuilder<VideoPlayerValue>(valueListenable: _controller!, ...)` that returns `SizedBox.shrink()` when `value.isPlaying`. Remove the now-redundant `setState` wrapper in `_togglePlayPause` ONLY if nothing else in `build` depends on it — the seek-indicator and `_shouldPause` post-frame check at lines 224-230 still read state in `build`, so keep `setState` if in doubt (keeping it is harmless).

**Verify**: `flutter analyze` → no errors. Manual: tap to pause → title appears immediately; tap to play → disappears immediately; when a video ends by itself the title also appears.

### Step 4: Remove dead null checks in `_seekVideo`

Delete the `currentPosition != null && duration != null` condition (both are
non-nullable) and unindent the body.

**Verify**: `flutter analyze` → the corresponding `unnecessary_null_comparison` infos are gone; total issue count did not increase.

## Test plan

`flutter test` must stay green. Manual script (state device used in the
completion report; if no device, mark row `BLOCKED (manual verification pending)`):
1. Fast-fling stress: 30s of rapid vertical flings → no crash.
2. Pause/play tap → title overlay toggles instantly.
3. A chunk shorter than 2s (create one with ffmpeg if handy: `ffmpeg -f lavfi -i testsrc=d=1:s=320x240 -pix_fmt yuv420p Short_S01E01_Part001.mp4`) renders without a NaN/exception on the progress bar.

## Done criteria

- [ ] `flutter analyze` — no errors, no `unnecessary_null_comparison` in this file
- [ ] `flutter test` — passes
- [ ] `grep -n "inSeconds / " lib/widgets/video_player_widget.dart` → no matches
- [ ] Init path never dereferences `_controller!` after an await (uses the captured local)
- [ ] Manual script done or row marked BLOCKED
- [ ] No files outside scope modified

## STOP conditions

- The file has been restructured beyond plan 003's changes (excerpts unrecognizable).
- After Step 1 videos no longer autoplay on first launch — the stale-controller guard is likely comparing wrongly; report rather than loosening the guard.

## Maintenance notes

- The captured-local pattern in Step 1 is the invariant to preserve: **any
  new `await` added to `_initializeVideo` needs the same post-await guard.**
- If controller pooling/preload management is ever added (see plan 007
  direction notes), this widget's lifecycle is the integration point.
