# garmin-connect-api

Read and write Garmin Connect from an MCP client such as Claude, from Python, or over a local HTTP
API: activities with per-set strength detail, daily health data, planned workouts, completed
strength sessions, and weigh-ins with full body composition.

<!-- mcp-name: io.github.happyc0der/garmin-connect-api -->

> **Unofficial.** Not affiliated with or endorsed by Garmin. It uses the same private endpoints as
> the Garmin Connect website, through [python-garminconnect](https://github.com/cyberjunky/python-garminconnect),
> because Garmin offers no public API for individuals. Those endpoints can change without notice,
> and using them is not sanctioned by Garmin's terms. It is meant for your own account and your
> own data.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.

**1. Log in once**, at a terminal:

```bash
uvx --from garmin-connect-api garmin login
```

This is the only place a password is ever typed. It asks for your Garmin email, password and any
multi-factor code, then stores tokens in `~/.garmin-api/default`, readable by your user only.
Nothing else ever prompts for a password; without tokens, every tool says to run this.

**2. Add the server to your MCP client.**

Claude Code:

```bash
claude mcp add --scope user --transport stdio garmin -- uvx garmin-connect-api
```

Claude Desktop, in `claude_desktop_config.json` (in `~/Library/Application Support/Claude/` on
macOS, `%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": ["garmin-connect-api"]
    }
  }
}
```

**3. Ask.** "How did I sleep this week?", "Did my pace hold up in yesterday's swim?",
"How has my resting heart rate moved since summer?", "Show my last strength session's sets, and
change sets 3 to 5 to dumbbell rows", "Schedule a swim for Thursday: 200 easy, then 8 x 100 free
on 20 seconds rest, in a 25-yard pool".

## What it can do

41 tools, each annotated so your client knows which only read (27), which add something (8) and
which delete or overwrite (6), and can ask before running those.

| Read | Write |
| --- | --- |
| Activities, filtered by type and date | Create a strength workout, schedule it, push it to a watch |
| Per-set strength detail: exercise, reps, weight | Create a run, ride or pool swim workout, with targets |
| Laps for runs and rides; pace per 100 for pool swims | Log a completed strength session, with every set |
| A day in one answer: steps, heart rate, stress, body battery, sleep, HRV, readiness | Correct a session's sets: exercise, reps, weight |
| Trends over up to two years: twelve metrics, daily, weekly or monthly | Record a weigh-in, or a full body composition reading |
| Personal records, named and in units | Rename an activity or change its description |
| Every weigh-in with body composition | Delete an activity, workout or weigh-in |
| Planned workouts and the calendar | |
| Garmin's 1,527-exercise catalogue, offline | |

Four tools are written for a model rather than mirrored from an endpoint: `garmin_status` checks
the login without calling Garmin, `find_exercise` finds Garmin's name for a lift, and
`recent_strength_sessions` and `week_summary` answer common questions in one call.

**Logging a finished workout** is the write Garmin's own developer programme has never offered. A
session is encoded as a FIT activity file with set messages and uploaded, so it appears in Garmin
Connect as a Strength Training activity, exercise by exercise.

**Body composition** from a smart scale (fat, water, skeletal muscle, bone, BMI, basal metabolic
rate, visceral fat) is written as a FIT weight-scale file, the way Garmin's own Index scales do it.
A plain weigh-in carries weight alone.

## Before you write anything

- **Activities you log may be shared automatically.** If your Garmin account is connected to Strava
  or another app, an uploaded session can be posted there too, and deleting it from Garmin does not
  delete it there.
- **Rate limits are taken seriously.** Garmin puts Cloudflare and per-account rate limits in front
  of its login, and a run of failed logins can lock an account out for a day or more. Nothing here
  retries a login, and a rate limit stops every call path rather than being retried. If you see
  one, wait.
- **Exercises need Garmin's own names**, such as `BENCH_PRESS` / `BARBELL_BENCH_PRESS`. Every write
  checks them against the catalogue first and suggests near misses, so a typo fails with a useful
  message rather than Garmin's bare 400. Common gym names work in search: "rdl", "ohp", "pull ups".
- **Sessions and body-composition readings need a timezone offset**, or a 7am reading would land
  hours out. They are refused without one.

## Command line

```bash
garmin doctor                          # setup check, no network
garmin activities --type strength_training --limit 5
garmin sets <activity-id>              # per-set detail
garmin day 2026-10-18                  # one day of health data
garmin exercises "lat pulldown"        # search the catalogue, offline
garmin workout create workout.json     # plan a strength workout
garmin log-session session.json        # log a completed one
garmin encode-session session.json     # build its FIT file without uploading
garmin decode-fit activity.zip         # read sets out of a Garmin export
garmin serve                           # the HTTP API on 127.0.0.1:8765
```

Run them with `uvx --from garmin-connect-api garmin ...`, or install with
`uv tool install garmin-connect-api`. Example specs:
[session.json](https://github.com/happyc0der/garmin-connect-api/blob/main/examples/session.json),
[workout.json](https://github.com/happyc0der/garmin-connect-api/blob/main/examples/workout.json).

An encoded session file can also be imported by hand on the Garmin Connect website, if you would
rather nothing logged in on your behalf.

## Python library

```python
from datetime import datetime, timedelta, timezone
from garmin_api.services import activities, health, uploads
from garmin_api.models.workouts import (
    StrengthSessionSpec, StrengthSessionExercise, StrengthSessionSet,
)

for activity in activities.list_activities(limit=5, activity_type="strength_training"):
    sets = activities.get_sets(activity.activity_id)
    print(activity.name, sets.total_volume_kg, "kg")

print(health.day("2026-10-18").steps)

uploads.log_strength_session(StrengthSessionSpec(
    name="Push A",
    start_time=datetime(2026, 10, 18, 7, 0, tzinfo=timezone(timedelta(hours=-4))),
    exercises=[StrengthSessionExercise(
        category="BENCH_PRESS",
        exercise_name="BARBELL_BENCH_PRESS",
        sets=[StrengthSessionSet(reps=10, weight_kg=60.0, rest_s=120)],
    )],
))
```

Responses are pydantic models with Garmin's inconsistencies ironed out: kilograms rather than
grams, timezone-aware instants, snake_case throughout. Garmin's own payload is still on `raw` when
something is not mapped.

## HTTP API

`garmin serve` runs the same API on `127.0.0.1:8765`, with interactive docs at `/docs`. The MCP
tools are generated from these routes, so the two cannot drift apart. Because it can write to your
account, the service refuses writes from web pages open in your browser and requests naming any
host but `localhost`, which defeats DNS rebinding. Binding beyond localhost requires
`GARMIN_API_KEY`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `GARMINTOKENS` | Where tokens are stored. Defaults to `~/.garmin-api/default`. |
| `GARMIN_API_KEY` | Required to bind the HTTP service beyond localhost. |
| `GARMIN_API_HOSTS` | Extra host names the HTTP service answers to, comma-separated. |

## Development

```bash
git clone https://github.com/happyc0der/garmin-connect-api
cd garmin-connect-api
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

231 offline tests, needing no network and no account, run on Linux, macOS and Windows. They cover the models against real Garmin
response shapes, the FIT codec's round trip, every one of the 1,527 catalogue exercises against
the FIT profile, the HTTP routes, and every MCP tool's output against its own advertised schema,
which strict clients enforce.

Tests against a real account are opt-in, and the ones that create data need a second opt-in:

```bash
GARMIN_LIVE=1 uv run pytest tests/live                      # read only
GARMIN_LIVE=1 GARMIN_LIVE_WRITE=1 uv run pytest tests/live  # creates, then deletes
```

## Why the unofficial route

Garmin's official Connect Developer Program is for businesses, has no endpoint that uploads a
completed activity, and was closed to new applicants in 2026. The aggregators that wrap it cost
hundreds of dollars a month and are read-only or plans-only. So this uses the endpoints Garmin's
own website uses, gently: no polling, no retries on a rate limit.

## Licence

MIT. Garmin and Garmin Connect are trademarks of Garmin Ltd., used here only to say what this
works with.
