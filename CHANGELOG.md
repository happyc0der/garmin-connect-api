# Changelog

## 0.1.0 (unreleased)

First public release.

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
