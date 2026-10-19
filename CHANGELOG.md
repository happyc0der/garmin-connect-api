# Changelog

## 0.2.1 (2026-10-19)

Fixes from a review of the whole package after 0.2.0, each reproduced before it was fixed and
covered by a test that fails on 0.2.0.

### Fixed

- **Pushing a workout to a watch** with no device named sent it to Garmin's "last used device",
  which can be a "Default" pseudo-device left by an uploaded file or a smart scale, never a watch.
  A push now goes only to a device on the account's list: the last used if listed, else the
  primary activity tracker, else the only device. The reply names the device. Checked live:
  Garmin queued the workout for the watch.
- **Logging a session.** A re-upload answered with Garmin's 409 is reported as a duplicate, with
  the existing activity's id. After an "accepted" upload, an activity that was already at the
  session's start, such as the watch's own recording, is never taken for the new one and renamed.
  A failed lookup no longer hides a successful upload. An exercise given by category alone is the
  category's generic entry, in sessions and planned workouts alike.
- **FIT files.** Fields a watch leaves empty, such as the weight of a bodyweight set, read as
  missing instead of 4,095.94 kg. The session's own start time is used. Rests across a
  daylight-saving change last as long as they say. Timed sets carry no reps. A renamed session
  keeps its serial, so Garmin still sees it as a duplicate.
- **Health data.** Sleep SpO2 and respiration were always empty and now come through. The day
  summary's training readiness is the morning score, as the trend's is. Steps are unknown rather
  than zero when Garmin does not answer. Personal records are no longer left unnamed by one failed
  request. A weigh-in given in another timezone's time lands on the account's own local day.
- **Errors.** A read that gets a 401 reloads the stored tokens and retries once, so a running
  server recovers after `garmin login`. A network failure on a write is "Garmin unavailable", not a
  crash. Unexpected replies are a clear 502 rather than a 500, which over MCP read as the model's
  own invalid input. Error text keeps its advice, such as "Run `garmin login`".
- **Workouts.** Target ranges are accepted either way round; an impossible pace is refused up
  front instead of crashing the build. A workout created but not scheduled says its id, so a retry
  does not make a second copy.
- **Security.** Writes are refused from web pages served by any other local server, not just other
  hosts. `garmin mcp --http` now has the HTTP API's guard: host and origin checks, the bearer key
  when `GARMIN_API_KEY` is set, and no binding beyond localhost without one.
- **Exercise names.** `match_exercise` no longer turns one lift into another ("Hack Squat" became
  "Back Squats"). Search understands plurals such as "bench presses", "crunches" and "lat
  pulldowns", and nicknames inside a name, such as "dumbbell rdl".
- **Command line.** Spec files saved with a byte-order mark, or as UTF-16 by Windows PowerShell,
  are read correctly. `--out` may be a directory. A rejected upload exits 1.
- `edit_activity_sets` is no longer advertised as idempotent; `get_activity_sets` includes the
  total volume; `end_date` without `start_date` is refused instead of ignored.

## 0.2.0 (2026-10-19)

First public release. 0.1.0 was prepared but never published; its contents are listed below
under "Foundations".

### Added

Five features, each checked against a real account.

- **Laps and lengths.** `get_activity_laps` gives a run's or ride's laps, and a pool swim's pace
  per 100 of the pool's own unit (yards in a yard pool), with first half against second half and
  the fastest and slowest stretch. Ride averages use moving time, as Garmin's own do.
- **Swim, run and ride workouts.** `create_endurance_workout` builds steps that end on a time, a
  distance or the lap button, with repeats; runs and rides take pace, speed, heart-rate, power or
  cadence targets; swims take a stroke, equipment, a pool length and rests on the wall.
- **Trends.** `get_trend` follows one of twelve metrics (steps, resting heart rate, sleep and sleep
  score, HRV, readiness, stress, body battery, weight, body fat, running and cycling VO2 max) over
  up to two years, daily, weekly or monthly, usually in one request.
- **Editing sets.** `edit_activity_sets` corrects the exercise, reps or weight of a strength
  activity's sets, or deletes sets, for when a watch guesses the exercise wrong.
- **Named records.** Personal records now carry a name, a unit and a readable value, such as
  "Fastest 1 km run, 4:47.5", instead of Garmin's bare type number.

### Foundations

- **Library, HTTP service and MCP server over one core.** The MCP tools are generated from the
  FastAPI routes, so every endpoint is a tool with the same name and description.
- **Read:** activities with per-set strength detail and training volume; daily health (steps,
  heart rate, stress as minutes per band, body battery, sleep with stages, HRV, training readiness
  and status); every weigh-in with body composition; original activity files.
- **Write:** strength workouts onto the calendar, scheduled and pushed to a watch; completed
  strength sessions as FIT activities with their sets; weigh-ins with full body composition as
  FIT weight-scale files; activity renames and deletions.
- **Every MCP tool is annotated** with a title and whether it reads, adds, or deletes and
  overwrites, so clients can ask before the destructive ones.
- **Safety:** passwords are typed only at `garmin login`; rate limits stop every call path instead
  of retrying; the local service refuses cross-origin writes and foreign host names, and needs a
  key beyond localhost.
