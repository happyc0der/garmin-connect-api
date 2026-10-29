# Changelog

## 0.3.2 (2026-10-28)

### Fixed

- **A trend reaching today summarised today's running count as a finished day.** Asked at 9am
  for 30 days of steps, `get_trend` reported the morning's few hundred steps as the minimum and a
  drop of thousands as the change, which was only the clock. For steps, stress and body battery,
  which Garmin keeps adding to until midnight, the summary now covers the days before today and
  names today under `in_progress`; today's value stays in the points. A week ending today no
  longer counts as a whole week. Sleep, HRV, readiness and weight are settled by morning and keep
  today. Reported, with the fix's shape, by [@mattabate](https://github.com/mattabate) in #11.

## 0.3.1 (2026-10-19)

### Fixed

- **The MCP handshake reported FastMCP's version** (4.1.1 with the dependencies 0.3.0 resolved
  to) instead of this package's, so a client or directory reading the server's version showed
  the wrong one. It now reports the package version.

## 0.3.0 (2026-10-19)

The MCP tools, rewritten for the model that reads them, against Glama's tool definition quality
rubric. The HTTP API is unchanged.

### Changed

- **Three tools leave the MCP server**, as duplicates that only made a model choose:
  `search_exercises` (the same search as `find_exercise`), `healthz` (`garmin_status` reports the
  same login) and `get_last_activity` (`list_activities` with a limit of 1). All three remain on
  the HTTP API. 38 tools remain.
- **Descriptions draw the lines between siblings**: the single-day health tools against
  `get_day_summary`, `week_summary` and `get_trend`; `find_exercise` against `match_exercise`;
  workouts against sessions; `edit_activity_sets` against `update_activity`. They say what each
  tool returns and what a write changes, including that deleting a set renumbers the ones after
  it, so a repeated delete removes a different set.
- **Every argument is described, nested ones included**: a workout step's end and pace target, a
  set's reps and weight, each body composition reading with its unit, and which tool supplies
  each id. A test now fails on any argument without one.
- `get_trend`'s description no longer says readiness is fetched four weeks at a time; 0.2.6
  changed that to a year.

### Added

- **A Dockerfile** for the stdio server, for directories that build a server to inspect it.
  Tokens live in `/tokens`; the README shows how to mount them and run as your own user.

## 0.2.6 (2026-10-19)

### Changed

- **Readiness trends take a twelfth of the requests.** Measured against a real account, Garmin
  answers readiness for at least 367 days per request, so a two-year readiness trend is now 2
  requests instead of 27; it had been fetched four weeks at a time while the limit was unknown.
  The same check confirmed HRV's limit (366 days apart) and that a pool swim's length is given in
  the pool's own unit, as the lap tools read it.

## 0.2.5 (2026-10-19)

A fifth review round: a cold read of the whole package by a reviewer new to it, and 0.2.4's own
changes checked for regressions. Each finding was reproduced before it was fixed, and each fix has
a test that fails without it. The findings now take unusual conditions to show, but two gave
wrong answers to ordinary questions.

### Fixed

- **Laps.** A lap's pace was worked out on elapsed time and the summary's on moving time, so a lap
  with a stop in it read slower in the list than in the summary, and could be named the fastest
  while showing the slowest pace. The first and second halves split the laps by count, so a 9 km
  lap and a 1 km lap made a "second half" of 1 km. Laps are on moving time now, and the halves
  split the distance.
- **The body fat trend** dropped any day whose last weigh-in carried weight only, though an
  earlier reading that day had body fat.
- **Weigh-ins and timezones.** `add_weigh_in` placed a time with no offset, or no time at all, in
  the machine's timezone through the library; it now uses the account's, and refuses when that is
  unknown. A time given with its offset is sent as given when the account's timezone cannot be
  read, rather than refused. `tzdata` is now a dependency, so systems without their own timezone
  database can load the account's.
- **Data Garmin withholds as private** read as an empty day; it is now reported as withheld, and
  a day summary lists any part it could not read, and why.
- **Behind a proxy that ends TLS**, the service's own /docs page could no longer write.
- **Uploads.** A reply that could not be read was a success over HTTP and MCP but a failure in the
  CLI; it is an error everywhere. A failed connect is no longer repeated by every part of a day
  summary, and a rate-limit note is no longer replaced by advice to make another call.
- Resting heart rate trends are fetched a year at a time, as the library advises.
- A last check of these changes: a weigh-in given in UTC is refused while the account's timezone
  is unknown, since its day cannot be told; odd timezone names and Origin schemes are handled; a
  new login clears the short holds after a rate limit or an unreachable Garmin.

## 0.2.4 (2026-10-19)

A fourth review round: 0.2.3's own changes checked for regressions, and a security and
data-integrity audit of the whole package by a reviewer new to it. Each finding was reproduced
before it was fixed, and each fix has a test that fails without it. Upgrading from 0.2.3 is
recommended: its first item can lock a running server out of the account.

### Fixed

- **One network blip locked a 0.2.3 server out of the account.** A dropped connection or a 503
  while loading the profile, which the library reports as an authentication error, was held
  against the stored tokens until a restart or a new login, with every call saying "run `garmin
  login`"; a reply Garmin marks privacy-protected did the same to every tool. Only a 401 or 403
  holds the tokens now, the hold lapses after ten minutes, and a network failure is reported as
  one.
- **A retried set edit could delete the wrong set.** When Garmin applied an edit but could not
  read it back, the tool reported an error, and a retried "delete set 2" deleted whichever set was
  set 2 by then. The edit is now reported as applied, with the sets as written.
- **A name FastAPI's test client uses was trusted.** A page under the name `testserver`, pointed
  at 127.0.0.1 by a hostile network, could call every write tool of `garmin serve` or
  `garmin mcp --http`. It is no longer trusted.
- **Any web page could spend the account's Garmin requests** while the HTTP service ran, with
  cross-site GETs such as an image tag. Requests a browser marks cross-site or same-site are
  refused, and so is a foreign Origin on any method. Responses forbid framing.
- **An exercise given by its display name alone** was saved as the category's generic exercise.
  The name is now looked up, and refused if it names no exercise in the category.
- **Times without an offset** are refused if the account's timezone cannot be read, instead of
  being placed in the machine's.
- A small zip could expand to gigabytes when decoded; FIT data is capped at 32 MB.
- An odd value in a day's stats no longer sinks the whole day summary, or the week; a connect
  Garmin rate limits holds calls for a minute instead of each trying again; a rate limit while
  naming a logged session stops there.

### Documentation

- SECURITY.md said logging out and in again replaces leaked tokens. Logging out deletes only the
  local copy; it now says to change the Garmin password.

## 0.2.3 (2026-10-19)

A third review round: 0.2.2's own changes checked for regressions, concurrency and process
lifetime, end-to-end journeys driving the MCP as a model would against a stateful fake Garmin, and
the fuzz and property harnesses rerun and extended. Each finding was reproduced before it was
fixed, and each fix has a test that fails without it.

### Fixed

- **Logging out or in while a server runs.** A running server ignored `garmin logout`, and a
  login as someone else, and kept using the old account; later it wrote that account's refreshed
  tokens back over the new login's. `garmin login` itself resumed stored tokens and ignored the
  password typed. Logins now leave a marker the server checks, and a dropped client can no longer
  write tokens back.
- **A rate limit met while connecting** was reported as "run `garmin login`", which on a throttled
  account leads straight to the SSO lockout. It is reported as a rate limit.
- **Several calls at once.** Two edits to one activity's sets could lose one; concurrent 401s each
  rebuilt the client, and refused tokens were tried again by every call; uploads blocked the
  service; two sessions logged at once could each claim the other's activity.
- **Times without an offset** are taken as the account's own local time when logging a session or
  recording a weigh-in, instead of being refused, which left a model to guess UTC. The summary
  tools give local start times and the account's timezone, so an evening session no longer reads
  as the next day's.
- **A misspelt tool argument** was silently dropped: `create_strength_workout(date=...)` saved the
  workout and never scheduled it. Unknown arguments are refused, naming the ones the tool takes.
- **Listing the MCP server's resources** failed on every client.
- **Replies that guide the next step.** Logging a session says when Garmin has not finished, so
  the name and notes are not applied yet; when another activity starts at the same second; when
  several new activities could be the upload; and how long the session is. A find_exercise result
  can be passed on whole.
- **Trends.** The change is between whole weeks or months, not a stub at either end; a day two
  windows both return counts once.
- **The day summary** reports an outage when every part fails, and one odd value costs only its
  part.
- **0.2.2's regressions.** Pages on other local ports could write again when the Host header had
  no port; "Push-ups" and other hyphenated plurals stopped matching; decoding made rests out of
  rounding; a decoded set the watch could not identify could not be logged.
- Smaller: a deleted set takes its rest with it; `garmin workout unschedule`; numbers are never
  typos in exercise matching; infinite numbers get a 422, not a 500.

## 0.2.2 (2026-10-19)

A second review round: a check of 0.2.1's own fixes for regressions, fuzzing of every route and
tool against odd Garmin replies, property tests of the FIT codec and the exercise search, every
promise in the docs checked against the code, and every Garmin field checked against
python-garminconnect's own models. Each finding was reproduced before it was fixed, and each fix
has a test that fails without it.

### Fixed

- **Trends over long ranges failed outright.** Found live: Garmin refuses a stress or body battery
  range over 28 days and an HRV range over 366 days, so those trends failed past a month or a year.
  They are now fetched a window at a time, as the library already does for steps. VO2 max points
  Garmin groups by week or month are counted as periods, not days.
- **A search result saved the wrong exercise.** The search gives the variant as `exercise`; the
  write tools called it `exercise_name` and ignored unknown keys, so "Barbell Bench Press" was saved
  as plain "Bench Press". Both names are accepted, and unknown keys are now refused.
- **`match_exercise` could swap one lift for another** in ways 0.2.1 introduced ("Adduction" for
  "Abduction", "machine row" for the rowing machine), and lost some it used to find ("pullups").
  Search understands "-ies" plurals and no longer finds "to" in "toes".
- **An outage looked like no data.** Asked for sleep, HRV, stress, readiness or records alone, a
  Garmin outage answered as if nothing was recorded; it is now an error. The day summary still
  leaves out only the part that failed.
- **Logging sessions.** Notes become the activity's description instead of being dropped. A
  duplicate no longer renames the activity already there. A failed lookup before an upload no
  longer stops it. Decoding keeps the session's timezone, keeps sets the watch could not identify,
  and takes the time between sets as the rest.
- **Health data.** Readiness on accounts that get a single snapshot, fractional HRV values, the
  day's last weigh-in rather than the first listed, a set's likeliest exercise, and Garmin's
  "no reading" stress codes are all read correctly. Sleep times are never taken from Garmin's
  local fields as UTC.
- **Security.** An id holding an encoded slash could steer a tool to a different route; such paths
  are refused. MCP over HTTP no longer refuses writes from a same-origin client.
- **What a model reads.** The instructions and tool descriptions now say that a logged session may
  be shared to Strava, that relogging a corrected session makes a second activity, which night a
  date means, and what each trend's change measures (now the movement of the period averages).
  Creating a scheduled workout returns its schedule id.
- Impossible inputs (infinite distances, dates FIT cannot hold, sessions too long for its fields)
  are refused with a clear error instead of a 500.

### Note

A session's FIT file now records its local time to the exact second, so a session logged by 0.2.0
or 0.2.1 and logged again may not be recognised by Garmin as a duplicate.

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
  FastAPI routes, so each tool has its route's name and description (bulk downloads and file
  conversions stay HTTP-only).
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
