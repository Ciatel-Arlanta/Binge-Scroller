# Design: chunk manifest, episode browser, resume position

> Plan 009 deliverable. Evidence cites paths as of the post-plan-001–008 tree
> (commit near `60f0b46` / later). This is a design spike — **no product code
> changes** are required to accept this document.

## 0. Reconciliation with history

`git log --all --oneline -- '*manifest*'` returns:

- `b4cc2d4 Chunking Logic completed` — early monorepo scaffold; **no
  `manifest.json` schema or reader/writer** appears in that commit's tree
  (it added the Flutter app shell and chunker skeleton). There is nothing to
  merge with; this design is greenfield relative to product behavior.

## 1. Manifest schema (`manifest.json`)

### 1.1 Location and transport

- **One file per chunker output folder**: `{output_dir}/manifest.json`.
- Travels with the chunks when the user copies the folder to the phone
  (`README.md` workflow: USB → `Movies/BrokeBinge`). The app already treats
  that folder as the sole discovery root
  (`local_video_scroller/lib/services/video_service.dart` `getVideoDirectory`).

### 1.2 Top-level object

```json
{
  "schema_version": 1,
  "generated_by": "video-chunker",
  "generated_by_version": "0.1.0",
  "settings": {
    "target_duration": 120,
    "strategy": "smart",
    "vertical_format": "blur",
    "output_resolution": "1080x1920",
    "burn_subtitles": true,
    "sub_lang": "eng"
  },
  "sources": {
    "Show.Name.S01E01.mkv": {
      "duration": 1420.5,
      "processed_at": "2026-04-08T12:00:00Z",
      "chunks": ["Show Name_S01E01_Part001.mp4", "..."]
    }
  },
  "chunks": [
    {
      "file": "Show Name_S01E01_Part001.mp4",
      "show": "Show Name",
      "season": 1,
      "episode": 1,
      "part": 1,
      "start": 0.0,
      "end": 118.4,
      "duration": 118.4,
      "source_file": "Show.Name.S01E01.mkv"
    }
  ]
}
```

Field notes:

| Field | Why |
|-------|-----|
| `schema_version` | App can refuse or migrate unknown versions. |
| `settings` | Debug + future “re-chunk with same knobs”. |
| `sources` | Upsert key for plan 007 resume re-runs (per source stem). |
| `chunks[].file` | Basename only — portable across machines. |
| `start`/`end`/`duration` | Exact cut points; filenames cannot carry floats. |
| `show`/`season`/`episode`/`part` | Single source of truth; app stops owning a parallel regex. |

### 1.3 Writer behavior (chunker)

Evidence of today’s dual contract:

- Writer name format: `chunker.py` `process_single_video` builds
  `f"{show_name}_S{season}E{episode}_Part{part_str}.mp4"`.
- Reader regex: `lib/models/video_model.dart` `VideoModel.fromPath`
  `RegExp(r'(.+?)_S(\d+)E(\d+)_Part(\d+)\.mp4')`.

**Merge / upsert rules** (ties to plan 007 resume):

1. Load existing `manifest.json` if present; else start empty `schema_version: 1`.
2. After a source file finishes successfully, **replace** all `chunks` entries
   whose `source_file` equals that source’s basename, and refresh
   `sources[source]`.
3. Leave entries for other sources untouched.
4. Write via temp file + `os.replace` (same pattern as plan 007 chunk outputs)
   so a crash mid-write cannot leave a half JSON.
5. Chunk rows are written from the actual `cut_points` list after the plan 002
   tail append — durations must match what was encoded.

**Non-goal for v1:** rewriting filenames. Manifest rides alongside the existing
name convention so old phones without a reader still work.

### 1.4 Reader behavior (app)

1. After resolving the video directory, if `manifest.json` exists and
   `schema_version` is supported, build `List<VideoModel>` from `chunks`
   (path = `dir/file`, metadata from fields). Prefer manifest order
   (show → season → episode → part); do not re-sort by raw path.
2. **Fallback:** if missing/unreadable/unsupported version, keep today’s
   `directory.list` + `VideoModel.fromPath` path
   (`video_service.dart` `getAllVideos`). Old folders keep working.
3. Optionally verify each `file` exists; drop missing rows and log once
   (partial USB copies).

### 1.5 Optional test sketch (not wired)

Fenced for a future `video-chunker/tests/test_manifest.py` — do not add in this plan:

```python
def test_upsert_replaces_only_one_source(tmp_path):
    # write manifest with source A + B chunks; re-process A; B rows unchanged
    ...
```

## 2. Episode browser

### 2.1 Evidence of unfinished intent

- `VideoService.getVideosByEpisode()` (`video_service.dart`) builds
  `Map<episodeKey, List<VideoModel>>` sorted by part — **no screen calls it**.
- Feed is a flat `PreloadPageView` over `videoState.videos`
  (`video_feed_screen.dart`).

### 2.2 Immediate data bug (call out for slice (a))

`getAllVideos` sorts with `files.sort((a, b) => a.path.compareTo(b.path))`.
With ≥2 shows, alphabetical **path** order interleaves shows
(`Alpha_S02...` before `Beta_S01...` is fine, but `Show_S01E02_Part001` can
sort after `Show_S01E10_Part001` only if zero-padding holds — parts are padded,
episodes are padded in the filename contract, but **different shows still
interleave** in one vertical binge). Desired order:

`showName ASC → season ASC → episode ASC → part ASC`

using parsed fields (or manifest fields). This is an **S-effort fix** independent
of the browser UI.

### 2.3 UX sketch

- Entry: app-bar / long-press / edge drawer on `VideoFeedScreen` (no new
  route host required).
- Tree: **Show → Season → Episode** (episode row shows part count + watched
  check from §3).
- Tap episode → set feed `currentIndex` to that episode’s **first unwatched
  part**, else part 1; call existing `updateIndex` so
  `last_watched_path` stays consistent
  (`video_state_provider.dart`).
- Feed remains a **flat list of all chunks** (not a filtered sub-list) so
  swipe-up still crosses episode boundaries — browser is navigation, not a
  mode switch. Alternative (filtered feed) is rejected for v1: it breaks
  “endless scroll” muscle memory and complicates preload indices.

### 2.4 Provider shape

- Keep `List<VideoModel> videos` as source of truth.
- Add derived `Map` / getters wrapping `getVideosByEpisode` logic (move
  grouping into the provider or a pure function on the list) so the drawer
  does not re-scan disk.
- `jumpToEpisode(show, season, episode)` → index search on `videos`.

## 3. Resume & watch-state

### 3.1 Today

`VideoStateProvider` persists only `last_watched_path`
(`shared_preferences`). In-chunk position is lost on background kill;
cross-show context is a single cursor.

### 3.2 Proposed store

Key: `watch_state_v1` (JSON string) or a small file beside the videos
folder is **not** preferred (app-specific storage is wiped; SharedPreferences
survives reinstall less often than MediaStore files but matches current
stack). Prefer SharedPreferences for v1.

```json
{
  "/storage/.../Show_S01E01_Part003.mp4": {
    "positionMs": 42000,
    "watched": false,
    "updatedAt": 1710000000
  }
}
```

Rules:

- `watched: true` when `onVideoEnd` fires (plan 003 latch) or
  `positionMs / durationMs >= 0.9`.
- On open of current item, `seekTo(positionMs)` once after init (plan 004
  cancellation-safe init must still apply).
- **Prune:** max 500 entries; drop oldest `updatedAt` when over cap; drop keys
  whose path is not in the current `videos` list after a successful library
  load.
- `last_watched_path` remains the cold-start index hint (cheap); watch map is
  the per-chunk detail.

### 3.3 `VideoModel.toJson` / `fromJson`

Evidence: `video_model.dart` defines both; nothing in `lib/` persists them
today. **Recommendation:** keep and extend for manifest round-trip
(`durationSec`, optional) — they become the in-memory DTO for manifest rows
and watch-state keys (`path`). Do **not** delete; wire them in slices (c)/(d).

## 4. Build-out slices

| Slice | Work | Effort | Depends | Verification |
|-------|------|--------|---------|--------------|
| **(a)** Sort order fix | `getAllVideos` sort by show/season/episode/part | S | — | Unit test on unsorted paths; two-show fixture orders correctly |
| **(b)** Manifest writer | chunker upsert `manifest.json` after each source | M | 007 resume semantics | Re-run one source; other sources’ rows intact; temp+replace |
| **(c)** Manifest reader + fallback | app loads manifest; fallback to `fromPath` | M | (b) useful but testable with fixture file | Fixture folder with/without manifest; missing file rows skipped |
| **(d)** Resume position | watch map + seek on open + watched flag | M | 003/004 | Kill app mid-chunk; reopen seeks; prune test |
| **(e)** Episode browser UI | drawer + `jumpToEpisode` | M | (a); (d) nice-to-have for checks | Tap episode jumps index; feed still full list |

Independence:

- **(a)** alone is shippable and high value — do first.
- **(b)** and **(c)** pair; (b) can land without app changes.
- **(d)** does not need the manifest (path keys work with filenames).
- **(e)** needs (a); works without (b)–(d) but is weaker without (d).

## 5. Open questions (recommended answers)

1. **Manifest basename vs absolute path on device?**  
   **Rec:** basename in JSON; app prefixes `getVideoDirectory()`. Portable.

2. **Multiple output folders / multi-library?**  
   **Rec:** v1 single folder (README). Multi-root is a later SAF picker
   (plan 005 maintenance notes).

3. **Should chunker refuse to run if manifest schema is newer?**  
   **Rec:** yes, exit non-zero with message; never down-write.

4. **Watch state in SharedPreferences vs file in Movies/BrokeBinge?**  
   **Rec:** SharedPreferences v1 (no extra storage permission). Revisit if
   users want progress to sync via USB folder copy.

5. **Episode browser: filter feed vs jump in full feed?**  
   **Rec:** jump in full feed (§2.3).

6. **Include subtitle track id in manifest?**  
   **Rec:** defer; not needed for playback of already-burned chunks.

## 6. Out of scope (explicit)

- Changing filename regex or dropping filename metadata in v1.
- Cloud sync, accounts, analytics.
- iOS/desktop library UX (Android primary).
- Implementing slices (a)–(e) in this plan — convert chosen slices via the
  improve skill into numbered build plans.
