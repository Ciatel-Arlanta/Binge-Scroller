# Plan 005: Make the app look where the README tells users to put videos (storage + permissions)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. When done, update your row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- local_video_scroller/lib/services local_video_scroller/lib/screens local_video_scroller/android/app/src/main/AndroidManifest.xml README.md`
> On mismatch with the excerpts below, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED — storage behavior differs across Android versions; needs on-device verification.
- **Depends on**: plans/001-verification-baseline.md
- **Category**: bug
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters

The README (lines 72-74) tells users to create a folder named **`BrokeBinge`**
on the device's internal storage and copy chunks there. The app actually looks
in the **app-specific** external directory
(`/storage/emulated/0/Android/data/<package>/files/Movies/BrokeBinge`) — a
location users can't easily reach and that is wiped on uninstall. Users who
follow the README get "No videos found in BrokeBinge folder". Meanwhile the
manifest requests broad permissions (`MANAGE_EXTERNAL_STORAGE`,
`READ_MEDIA_IMAGES`, legacy write) that the app-specific directory doesn't
need at all, and the permission-denied dialog has an OK button that does
nothing.

**Decision taken by this plan** (do not re-litigate): read from the **shared**
`Movies/BrokeBinge` directory (`/storage/emulated/0/Movies/BrokeBinge`) using
`READ_MEDIA_VIDEO` on Android 13+ and `READ_EXTERNAL_STORAGE` below, matching
the README's promise. This keeps the "copy files over USB" workflow and
survives reinstalls. Drop `MANAGE_EXTERNAL_STORAGE`, `READ_MEDIA_IMAGES`, and
`WRITE_EXTERNAL_STORAGE`.

## Current state

- `local_video_scroller/lib/services/video_service.dart:8-35` — `getVideoDirectory`:

```dart
final directory = await getExternalStorageDirectories(type: StorageDirectory.movies);
if (directory != null && directory.isNotEmpty) {
  final targetDir = Directory('${directory.first.path}/$_defaultDirectory');
```

`getExternalStorageDirectories` (path_provider) returns app-specific dirs
under `Android/data/...`, NOT the shared Movies folder.

- `video_service.dart:47-48` — listing filters `entity.path.endsWith('.mp4')` (misses `.MP4`).
- `local_video_scroller/lib/screens/video_feed_screen.dart:61-73` — `_checkPermissions`: requests `Permission.videos`, falls back to `Permission.storage`; no handling of `isPermanentlyDenied`; dialog (lines 75-93) offers only a no-op OK.
- `local_video_scroller/android/app/src/main/AndroidManifest.xml:2-6` — declares `WRITE_EXTERNAL_STORAGE`, `READ_EXTERNAL_STORAGE (maxSdk 32)`, `MANAGE_EXTERNAL_STORAGE`, `READ_MEDIA_VIDEO`, `READ_MEDIA_IMAGES`; line 8: `requestLegacyExternalStorage="true"`.
- `README.md:72-74` — instructs: create `BrokeBinge` on internal storage.
- Package already in pubspec: `permission_handler: ^12.0.1`, `path_provider: ^2.1.1`. No new packages needed.

**Caveat the executor must know**: reading a *shared* directory via raw
`dart:io` paths works on Android 13+ for media files when `READ_MEDIA_VIDEO`
is granted, and on ≤12 with `READ_EXTERNAL_STORAGE`. The hardcoded
`/storage/emulated/0/Movies` path is the standard primary-volume path; derive
it defensively (see Step 1) rather than hardcoding blindly.

## Commands you will need

| Purpose | Command (from `local_video_scroller/`) | Expected |
|---------|----------------------------------------|----------|
| Analyze | `flutter analyze` | no errors |
| Tests   | `flutter test`    | pass |
| Build   | `flutter build apk --debug` | exit 0 |
| Manual  | install on device, put an .mp4 in `Movies/BrokeBinge` | video appears in feed |

## Scope

**In scope**:
- `lib/services/video_service.dart`
- `lib/screens/video_feed_screen.dart` (permission flow + dialog only)
- `android/app/src/main/AndroidManifest.xml`
- `README.md` (folder-location paragraph only)
- `plans/README.md`

**Out of scope**: `video_player_widget.dart`, provider, iOS/desktop targets
(keep the existing documents-directory fallback for them), adding a folder-picker UI (future work).

## Steps

### Step 1: Point `getVideoDirectory` at shared Movies

In `video_service.dart`, for Android: resolve the shared path by taking
`getExternalStorageDirectories(type: StorageDirectory.movies).first.path` and
stripping the `Android/data/...` suffix — i.e. everything from `/Android/`
onward — then appending `Movies/BrokeBinge`. Concretely:

```dart
if (Platform.isAndroid) {
  final dirs = await getExternalStorageDirectories(type: StorageDirectory.movies);
  if (dirs != null && dirs.isNotEmpty) {
    final root = dirs.first.path.split('/Android/').first; // /storage/emulated/0
    final targetDir = Directory('$root/Movies/$_defaultDirectory');
    if (!await targetDir.exists()) {
      try { await targetDir.create(recursive: true); } catch (_) {}
    }
    return targetDir;
  }
}
```

Keep the existing non-Android fallback unchanged. Keep the debug `print` if
you like, but prefer `debugPrint`.

**Verify**: `flutter analyze` → no errors.

### Step 2: Fix the permission flow

In `video_feed_screen.dart` `_checkPermissions`:
- Request `Permission.videos` first; if the result is `PermissionStatus.denied`, request `Permission.storage` (covers Android ≤12 where `videos` reports denied by mapping).
- If either is granted OR limited → `_hasPermission = true`.
- If `isPermanentlyDenied` → show the dialog with **two** actions: "Open Settings" calling `openAppSettings()` (from permission_handler) and "Cancel".
- After returning from settings (app resumes), re-run `_checkPermissions` — hook into the existing `didChangeAppLifecycleState` `resumed` branch: if `!_hasPermission`, call `_checkPermissions()` there.

**Verify**: `flutter analyze` → no errors.

### Step 3: Trim the manifest

In `AndroidManifest.xml` remove: `WRITE_EXTERNAL_STORAGE`,
`MANAGE_EXTERNAL_STORAGE`, `READ_MEDIA_IMAGES`, and the
`requestLegacyExternalStorage` attribute. Keep `READ_MEDIA_VIDEO` and
`READ_EXTERNAL_STORAGE (maxSdkVersion 32)`.

**Verify**: `flutter build apk --debug` → exit 0.

### Step 4: Case-insensitive extension + update README

- In `video_service.dart` change the filter to `entity.path.toLowerCase().endsWith('.mp4')`.
- In `README.md`, replace the folder instruction with the exact path: `Internal storage → Movies → BrokeBinge` (i.e. `/storage/emulated/0/Movies/BrokeBinge`).

**Verify**: `flutter test` → pass; README shows the new path.

### Step 5: On-device verification

1. Fresh install (uninstall first so permissions reset).
2. Copy one chunk to `Movies/BrokeBinge` via USB/adb: `adb push x.mp4 /sdcard/Movies/BrokeBinge/`.
3. Launch → grant the video permission → the chunk plays.
4. Deny the permission twice → the dialog offers "Open Settings"; granting there and returning makes the feed load without an app restart.

State the device/Android version in your report. If Android ≤12 hardware is unavailable, note that only 13+ was verified.

## Done criteria

- [ ] `flutter analyze`, `flutter test`, `flutter build apk --debug` all exit 0
- [ ] `grep -c "MANAGE_EXTERNAL_STORAGE" android/app/src/main/AndroidManifest.xml` → 0
- [ ] App lists videos from `/storage/emulated/0/Movies/BrokeBinge` on a real device/emulator
- [ ] README documents the same path the code reads
- [ ] No files outside scope modified

## STOP conditions

- On the test device, listing the shared directory with `dart:io` returns an empty list even though files exist and `READ_MEDIA_VIDEO` is granted (scoped-storage enforcement differs by OEM). Report the device/OS; the fallback decision (MediaStore query via a plugin, or SAF folder picker) is a scope change the maintainer must approve.
- `getExternalStorageDirectories` returns a path without `/Android/` in it.

## Maintenance notes

- A future "choose folder" feature (SAF) would supersede the hardcoded path;
  keep `getVideoDirectory` as the single source of truth for location.
- Reviewer: confirm no permission regressions on Android 10–12 (legacy path).
