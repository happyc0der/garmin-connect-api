# garmin-connect-api

Read and write Garmin Connect from Python, from HTTP, or from Claude.

One core with three faces: a typed library, a local HTTP service, and an MCP server generated
from that service so the tools can never drift from the API.

```bash
garmin login                      # once, at a terminal
garmin activities --type strength_training
garmin sets 987654321
garmin log-session examples/session.json
garmin serve                      # http://127.0.0.1:8765/docs
garmin mcp                        # stdio, for Claude
```

## What it can do

**Read.** Activities with their per-set strength detail: exercise, reps, weight, duration, and
the training volume computed from them. Daily health: steps, heart rate, stress, body battery,
sleep with stages, heart rate variability, training readiness and status, body weight and
composition. Original activity files in FIT, TCX, GPX or CSV.

**Write.** Strength workouts onto the Garmin calendar, scheduled and pushed to a watch.
Completed strength sessions into the activity history, with every set intact. Activity renames,
type changes and deletions. Weigh-ins, including full body composition from a smart scale: fat,
water, muscle, bone, BMI and metabolic age, written the way Garmin's own Index scales write them.

Logging a completed session is the part Garmin's own developer programme has never offered.
Their Training API publishes plans; nothing in it uploads a finished workout. This encodes the
session as a FIT file with set messages and posts it to the upload service, which is what the
Connect website itself uses.

## Install

Needs Python 3.12 or newer.

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

## Logging in

```bash
garmin login
```

The only place a password is ever typed. It prompts for the password and any multi-factor code,
then stores tokens under `~/.garmin-api/default`. The HTTP service and the MCP server read those
tokens and never prompt for anything; without them they answer 401 and tell you to run this.

Point `GARMINTOKENS` at another directory to store them elsewhere, or to share a login with
other tools that read the same format.

**On rate limits.** Garmin put Cloudflare fingerprinting and per-account rate buckets in front
of its login endpoints in March 2026. A run of failed logins can lock an account out for a day
or more. Nothing in this package retries a login, and a 429 from Garmin is surfaced as a 429
with an explanation rather than being swallowed and retried. If you see one, wait.

## Library

```python
from datetime import datetime, UTC
from garmin_api.services import activities, health, uploads
from garmin_api.models.workouts import (
    StrengthSessionSpec, StrengthSessionExercise, StrengthSessionSet,
)

for activity in activities.list_activities(limit=5, activity_type="strength_training"):
    sets = activities.get_sets(activity.activity_id)
    print(activity.name, sets.total_volume_kg, "kg")

print(health.day("2026-09-21").steps)

uploads.log_strength_session(StrengthSessionSpec(
    name="Push A",
    start_time=datetime(2026, 9, 21, 7, 0, tzinfo=UTC),
    exercises=[StrengthSessionExercise(
        category="BENCH_PRESS",
        exercise_name="BARBELL_BENCH_PRESS",
        sets=[StrengthSessionSet(reps=10, weight_kg=60.0, rest_s=120)],
    )],
))
```

Responses are pydantic models with Garmin's inconsistencies ironed out: weights in kilograms,
instants as timezone-aware datetimes, snake_case throughout. Garmin's local wall-clock times,
such as `start_time_local`, stay without a timezone, because Garmin does not say which one. Garmin's untouched payload is
still there on `raw` when something is not mapped.

## HTTP service

```bash
garmin serve
```

Binds to `127.0.0.1:8765`, with interactive docs at `/docs` and the schema at `/openapi.json`.
39 operations across activities, health, workouts, sessions and the exercise catalogue.

Binding anywhere other than localhost requires `GARMIN_API_KEY` to be set, and the service
refuses to start without it. The service can write to your Garmin account, so it should not be
reachable by anything you have not decided to let in.

## MCP server

```bash
claude mcp add garmin -- uv run --project /path/to/garmin-connect-api garmin mcp
```

Or, for Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/garmin-connect-api", "garmin", "mcp"]
    }
  }
}
```

The tools are generated from the HTTP routes, so every endpoint is a tool with the same name and
description. Four are hand-written because a model wants a different shape from a REST client:

| Tool | What it answers |
| --- | --- |
| `garmin_status` | Whether the server can reach the account. Call this first when something fails. |
| `find_exercise` | Garmin's category and exercise name for an exercise, which every write needs. |
| `recent_strength_sessions` | Recent strength workouts with their sets and volume, in one call. |
| `week_summary` | Seven days of training and health together. |

Bulky endpoints are deliberately excluded from the tool list, and remain on the HTTP API: raw
file downloads and sample-level activity detail are hundreds of kilobytes of numbers that would
flood a context window.
The health tools answer with summaries by default, such as minutes in each stress band rather than
480 three-minute readings, and take a flag when the raw readings are wanted.

## Naming exercises

Garmin identifies every strength exercise by a category and an exercise name, such as
`BENCH_PRESS` and `BARBELL_BENCH_PRESS`. It rejects anything outside its own catalogue. The
catalogue ships with this package, so searching it needs neither a network nor a login:

```bash
garmin exercises "bench press"
```

Every write validates against it first and answers an unknown name with near misses, rather than
letting Garmin reply with a bare 400.

## Logging a session

`examples/session.json` is a complete example. To see what would be sent without sending it:

```bash
garmin encode-session examples/session.json --out session.fit
```

That file can also be imported by hand on the Garmin Connect website, which is useful if you
would rather not have anything log in on your behalf. The reverse works too: `garmin decode-fit`
reads a session out of a FIT file or out of the zip Garmin's "Export Original" hands back.

The file's serial number is derived from the session, so the same session always encodes to the
same file. Garmin checks uploads for files it has already seen, and a repeat is reported with a
status of `duplicate` rather than as a second activity.

## Tests

```bash
uv run pytest
```

112 tests, no network and no account needed. They cover the models against real Garmin payload
shapes, the FIT codec's round trip, the whole 1,527-entry exercise catalogue against the FIT
profile, the HTTP routes, the generated MCP tool list, and every tool's output against its own
advertised schema, which strict MCP clients enforce.

Against a real account:

```bash
GARMIN_LIVE=1 uv run pytest tests/live                     # read only
GARMIN_LIVE=1 GARMIN_LIVE_WRITE=1 uv run pytest tests/live # creates and deletes real data
```

## How this talks to Garmin, and the caveat

Through `python-garminconnect`, which uses the same private endpoints as the Garmin Connect
website and mobile app. That is a deliberate choice, and the only one available:

- Garmin's official Connect Developer Program is for businesses, has no endpoint that uploads a
  completed activity, and has been closed to new applicants since spring 2026.
- The aggregators that wrap it (Terra, Rook, Spike, Thryve) start around $399 a month and are
  read-only or plans-only.

So this is unsanctioned by Garmin's terms, and the private endpoints can change without notice.
It is meant for your own account and your own data. Use it gently: no polling loops, no retries
on a 429.

## Licence

MIT.
