# Changelog

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
