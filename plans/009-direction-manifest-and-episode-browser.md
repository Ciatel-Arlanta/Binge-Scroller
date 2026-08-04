# Plan 009: Design spike — chunk manifest contract, episode browser, resume position

> **Executor instructions**: This is a **design/spike plan**, not a build
> plan. The deliverable is a written design (`plans/design-manifest.md`) plus
> at most a thin prototype behind no UI entry point. Follow the steps; on any
> STOP condition, stop and report. When done, update your row in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 8494213..HEAD -- video-chunker/chunker.py local_video_scroller/lib`
> Expect drift from plans 002–008; the *evidence* below is about which
> features exist, which survives those plans.

## Status

- **Priority**: P3
- **Effort**: M (design) — build-out estimated separately in the deliverable
- **Risk**: LOW — read-only investigation + a design doc
- **Depends on**: none (but read plans 005 and 007 first for context)
- **Category**: direction
- **Planned at**: commit `8494213`, 2026-07-18

## Why this matters (grounding evidence)

Three grounded signals in the repo point the same direction:

1. **Duplicated filename contract.** The chunker encodes metadata into
   filenames (`chunker.py:704`: `f"{show_name}_S{season}E{episode}_Part{part_str}.mp4"`)
   and the app re-parses them with a *separately maintained* regex
   (`lib/models/video_model.dart:19`). Any format change breaks the other side
   silently. A `manifest.json` written by the chunker and read by the app
   removes the dual regex and can carry data filenames can't (exact duration,
   source file, cut strategy).
2. **Unfinished intent in the app.** `VideoService.getVideosByEpisode()`
   (`lib/services/video_service.dart:62-82`) builds an episode→parts map that
   **no screen uses**, and `VideoModel.toJson/fromJson`
   (`video_model.dart:42-60`) exist with no persistence caller. Someone
   started an episode-browser / saved-state feature and stopped.
3. **Resume is index-only.** `VideoStateProvider` saves only
   `last_watched_path` (`lib/providers/video_state_provider.dart:51-53`);
   position within a chunk is lost, and jumping between shows resets context.
   For a binge app, per-episode progress is the core UX loop.

## Current state (facts to design against)

- Chunker output: flat directory of `.mp4` files, one folder for all shows.
- App discovery: lists `*.mp4` in one folder, sorts by path (`video_service.dart:47-53`).
- State: `shared_preferences` single key. Provider pattern via `provider` package.
- No backend, no network — everything is file-based; the manifest must travel with the files (same folder) since users copy the folder to the phone manually (README workflow, lines 101-106).

## Scope

**In scope (deliverables)**:
- `plans/design-manifest.md` — the design doc (see Step contents).
- Optionally `video-chunker/tests/test_manifest.py` sketches as fenced code in the doc — NOT wired-in code.

**Out of scope**: any change to `chunker.py`, `lib/`, or `pubspec.yaml`. This plan writes a document.

## Steps

### Step 1: Specify the manifest schema

Draft `manifest.json` (one per output folder, appended per processed file):

- Per chunk: `file`, `show`, `season`, `episode`, `part`, `start`, `end`, `duration`, `source_file`.
- Folder-level: `schema_version`, `generated_by`, chunker settings used.
- Define merge behavior for repeated runs (chunker re-run must upsert, not clobber — ties into plan 007's resume).
- Define the app's fallback: **filename parsing stays as the fallback** when no manifest exists (old folders keep working).

### Step 2: Specify the episode browser

Design (screens + provider changes, no code): a lightweight overlay or
drawer listing shows → episodes (data already available from
`getVideosByEpisode`), tap → jump feed to that episode's first unwatched
part. Include: how `currentIndex` maps to a filtered feed, and whether the
feed shows all shows interleaved (current behavior: alphabetical path sort
mixes shows — document this as the bug it becomes once ≥2 shows are present;
sorting should become show → season → episode → part using parsed fields, an
S-effort fix worth calling out for immediate implementation).

### Step 3: Specify resume & watch-state

Design per-chunk state in `shared_preferences` (or a small JSON file):
`{path: {positionMs, watched}}` with a size cap / pruning rule. Reuse the
existing unused `toJson/fromJson` on `VideoModel` if it fits, or call for
their deletion if not — dead code should not survive the design.

### Step 4: Estimate and slice

End the doc with build-out slices, each S/M-sized with its own verification,
ordered: (a) sort-order fix (immediate), (b) manifest writer in chunker,
(c) manifest reader + fallback in app, (d) resume position, (e) episode
browser UI. Note which slices are independent.

## Done criteria

- [ ] `plans/design-manifest.md` exists and covers Steps 1–4
- [ ] Every design choice cites the repo evidence it rests on (file:line)
- [ ] Open questions for the maintainer are listed in one section, each with a recommended answer
- [ ] No source files modified (`git status` shows only `plans/`)

## STOP conditions

- You find an existing manifest/persistence design in the repo or its history
  (`git log --all --oneline -- '*manifest*'` non-empty) — reconcile with it
  instead of designing fresh.

## Maintenance notes

- The maintainer chooses which slices to build; convert each chosen slice
  into its own numbered plan via the improve skill (`plan <slice>`).
