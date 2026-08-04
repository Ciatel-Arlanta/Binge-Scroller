# Plan 003: Stop audio playing in background and stop onVideoEnd re-firing (app)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. When done, update your row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- local_video_scroller/lib`
> On mismatch with the excerpts below, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW–MED — touches playback control flow; manual on-device check required.
- **Depends on**: plans/001-verification-baseline.md
- **Category**: bug
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

Two user-visible playback bugs:

1. **Backgrounding the app does not pause the video.** The lifecycle handler
   sets a flag but never triggers a rebuild, so no widget ever receives
   `autoPlay=false` — audio keeps playing when the user switches apps or locks
   the screen.
2. **`onVideoEnd` fires on every frame once a video completes.** The listener
   condition stays true after completion, so `nextPage()` is called repeatedly
   — queued page animations can skip several videos ahead.

## Current state

- `local_video_scroller/lib/screens/video_feed_screen.dart:37-59` — lifecycle handler:

```dart
case AppLifecycleState.paused:
  _isAppInBackground = true;
  // Pause all videos when app goes to background
  // This will be handled by the VideoPlayerWidget's didUpdateWidget method
  break;                                    // <-- no setState: nothing rebuilds
case AppLifecycleState.resumed:
  _isAppInBackground = false;
  setState(() {});                          // resume DOES rebuild
  break;
```

Also note line 39 obtains `videoState` via `Provider.of(..., listen: false)` but never uses it — remove the dead local.

- `video_feed_screen.dart:163-180` — `itemBuilder` computes `shouldAutoPlay = isCurrent && !_isAppInBackground` and passes it as `autoPlay`; `VideoPlayerWidget.didUpdateWidget` pauses when `autoPlay` flips false. This mechanism works only if the parent rebuilds.

- `local_video_scroller/lib/widgets/video_player_widget.dart:76-83` — completion listener:

```dart
_controller!.addListener(() {
  if (_controller!.value.position >= _controller!.value.duration &&
      widget.onVideoEnd != null &&
      widget.autoPlay &&
      !_isDisposed) {
    widget.onVideoEnd!();
  }
});
```

No latch: once `position >= duration`, every subsequent controller
notification calls `onVideoEnd!()` again. Also `duration` is
`Duration.zero` until initialization completes, and the listener is added
right after `initialize()`, so a completed-at-zero false positive is possible
on some platforms if the listener ever runs before the first frame.

- Repo conventions: plain `StatefulWidget`s, `provider` for state,
  no code generation. Match existing style in these two files.

## Commands you will need

| Purpose | Command (from `local_video_scroller/`) | Expected |
|---------|----------------------------------------|----------|
| Analyze | `flutter analyze` | no errors (infos OK) |
| Tests   | `flutter test`    | all pass |
| Manual  | `flutter run` on a device/emulator with videos in place | see Test plan |

## Scope

**In scope**: `lib/screens/video_feed_screen.dart`, `lib/widgets/video_player_widget.dart`, `plans/README.md`.

**Out of scope**: `lib/providers/video_state_provider.dart`, `lib/services/`, `lib/models/`, initialization/dispose race handling (that is plan 004 — do not restructure `_initializeVideo`/`_disposeController` here beyond what the steps say).

## Steps

### Step 1: Make backgrounding actually pause

In `video_feed_screen.dart` `didChangeAppLifecycleState`:
- Wrap the `paused` branch state change in `setState`: `setState(() { _isAppInBackground = true; });`
- Do the same symmetric form for `resumed` (move the assignment inside `setState`).
- Also treat `AppLifecycleState.inactive` and `hidden` the same as `paused` (screen lock passes through `inactive`/`hidden` before `paused`; pausing there is the correct UX for a video app). Leave `detached` as a no-op.
- Delete the unused `videoState` local at line 39.

**Verify**: `flutter analyze` → no new issues; the unused-variable info for `videoState` (if previously reported) is gone.

### Step 2: Latch the completion callback

In `video_player_widget.dart`:
- Add a field `bool _hasEnded = false;`.
- Extract the anonymous listener into a method `void _onControllerUpdate()` so it can be referenced (keeps the possibility of `removeListener` later; register it with `_controller!.addListener(_onControllerUpdate)`).
- In `_onControllerUpdate`, guard: fire `onVideoEnd` only when
  `!_hasEnded && value.duration > Duration.zero && value.position >= value.duration`,
  and set `_hasEnded = true` before invoking the callback.
- Reset `_hasEnded = false` when the user seeks backwards (`_seekVideo(false)` in this file, line 138-178) and when replay starts in `_togglePlayPause` while at the end (if position >= duration when play is pressed, seek to zero and reset the latch).
- Reset `_hasEnded = false` in `_initializeVideo` before adding the listener (covers controller re-creation on path change, video_player_widget.dart:41-44).

**Verify**: `flutter analyze` → no errors.

### Step 3: Manual on-device verification

With chunk files present (see README: `BrokeBinge` folder):
1. Play a video, press home → audio stops within ~1s. Reopen → current video resumes.
2. Lock the screen mid-video → audio stops.
3. Let a chunk play to the end → feed advances exactly one page, once.
4. On the last video of the feed, let it end → nothing happens, no exception in `flutter logs`.

**Verify**: all four behaviors observed; `flutter logs` shows no exception during them.

## Test plan

Widget-testing `VideoPlayerController` needs a platform fake and is out of
scope here (deferred in plan 001's notes). Verification for this plan is
`flutter analyze` + `flutter test` (existing suite stays green) + the Step 3
manual script. State in your completion report which device/emulator you used;
if you have no device available, complete steps 1–2, run analyze/tests, and
mark the plan row `BLOCKED (manual verification pending)` instead of DONE.

## Done criteria

- [ ] `flutter analyze` — no errors
- [ ] `flutter test` — passes
- [ ] `paused`/`inactive`/`hidden` branches call `setState` (grep: `_isAppInBackground = true` appears inside a `setState` closure)
- [ ] `_hasEnded` latch exists and gates `onVideoEnd`
- [ ] Step 3 manual script observed (or row marked BLOCKED per Test plan)
- [ ] No files outside scope modified

## STOP conditions

- The lifecycle handler or listener no longer matches the excerpts.
- Pausing on `inactive` causes videos to pause during in-app permission
  dialogs in a disruptive way you can observe — report the observation and
  apply the `paused`-only variant instead, noting the deviation.

## Maintenance notes

- If a "playlist/loop" feature is added later, the `_hasEnded` latch is the
  place replay logic must reset.
- Reviewer: check that no `setState` is called after `dispose` (the lifecycle
  observer is removed in `dispose`, video_feed_screen.dart:29-34 — that
  ordering must stay).
