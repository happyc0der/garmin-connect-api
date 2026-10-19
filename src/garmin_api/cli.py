"""The command line.

Also the only place a Garmin password is ever typed. ``garmin login`` runs at
a terminal, prompts for the password and any multi-factor code, and writes
tokens the service and the MCP then reuse. Neither of those ever prompts.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import typer

from . import __version__, auth, catalogue
from .errors import GarminApiError

app = typer.Typer(
    add_completion=False,
    help="Read and write Garmin Connect: activities, health data, workouts and sessions.",
    no_args_is_help=True,
    # A traceback must never print local variables: in `garmin login` one of
    # them is the password.
    pretty_exceptions_show_locals=False,
)
workout_app = typer.Typer(help="Planned workouts and the calendar.", no_args_is_help=True)
app.add_typer(workout_app, name="workout")


def _print(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


def _fail(message: str, detail: str | None = None) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    if detail:
        typer.secho(detail, fg=typer.colors.YELLOW, err=True)
    return typer.Exit(code=1)


def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a call, turning expected failures into a message instead of a traceback."""
    from pydantic import ValidationError

    try:
        return fn(*args, **kwargs)
    except GarminApiError as exc:
        raise _fail(exc.message, exc.detail) from exc
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise _fail("The spec file is not valid.", problems) from exc
    except FileNotFoundError as exc:
        raise _fail(f"No such file: {exc.filename}") from exc


def _read(path: Path) -> str:
    """A spec file's text: UTF-8, with or without a byte-order mark, or UTF-16.

    Never the locale's encoding, which on Windows mangles anything past ASCII;
    and PowerShell 5.1 writes UTF-16 with `>`.
    """
    data = _read_bytes(path)
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise _fail(f"{path} is not UTF-8 text.", "Save the spec file as UTF-8.") from exc


def _write(target: Path, data: bytes, default_name: str) -> Path:
    """Write output to a file, or into a directory under its default name."""
    if target.is_dir():
        target = target / default_name
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise _fail(f"Could not write {target}: {exc.strerror or exc}") from exc
    return target


def _print_upload(result: dict[str, Any]) -> None:
    """Print an upload's outcome, exiting 1 if Garmin refused it or answered oddly."""
    _print(result)
    if result.get("status") in ("rejected", "unknown"):
        raise typer.Exit(code=1)


def _read_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise _fail(f"No such file: {path}")
    return path.read_bytes()


@app.command()
def login(
    email: str = typer.Option(..., prompt="Garmin email", help="Asked for if not given."),
) -> None:
    """Log in to Garmin and store tokens for the service and the MCP.

    The password is prompted for, never taken as an argument, so it does not
    land in shell history. A multi-factor code is prompted for if Garmin asks.
    Tokens go to GARMINTOKENS if set, else ~/.garmin-api/default -- the one
    place every other command reads them from.
    """
    password = typer.prompt("Garmin password", hide_input=True)

    def prompt_mfa() -> str:
        return typer.prompt("MFA code")

    name = _run(auth.interactive_login, email, password, prompt_mfa)
    typer.secho(f"Logged in as {name}.", fg=typer.colors.GREEN)
    typer.echo(f"Tokens stored in {auth.token_dir()}")


@app.command()
def logout() -> None:
    """Delete the stored tokens."""
    # Only this package's own token files. The directory may be shared with
    # other Garmin tools through GARMINTOKENS, and their files are theirs.
    directory = auth.token_dir()
    removed = 0
    for file in auth._token_files():
        file.unlink()
        removed += 1
    auth.session().reset()
    typer.echo(f"Removed {removed} token file(s) from {directory}.")


@app.command()
def whoami() -> None:
    """Show who is logged in, and the state of the stored tokens."""
    if not auth.has_tokens():
        hint = (
            "Only old-format tokens found, which this version cannot use. Run `garmin login`."
            if auth.legacy_tokens()
            else "Not logged in. Run `garmin login`."
        )
        typer.secho(hint, fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    _print(
        {
            "name": _run(auth.call, "get_full_name"),
            "token_dir": str(auth.token_dir()),
            "token_age_days": round(auth.token_age_days() or 0, 1),
        }
    )


@app.command()
def doctor() -> None:
    """Check the setup without calling Garmin.

    Reports the version, whether tokens are present and how old they are, and
    that the bundled exercise catalogue loaded.
    """
    _print(
        {
            "version": __version__,
            "python": sys.version.split()[0],
            "token_dir": str(auth.token_dir()),
            "authenticated": auth.has_tokens(),
            "token_age_days": round(auth.token_age_days() or 0, 1) if auth.has_tokens() else None,
            "exercises_loaded": len(catalogue.all_exercises()),
            "categories": len(catalogue.categories()),
        }
    )


@app.command()
def activities(
    limit: int = 10,
    activity_type: str | None = typer.Option(None, "--type"),
    start_date: datetime | None = typer.Option(None, "--from", formats=["%Y-%m-%d"]),
    end_date: datetime | None = typer.Option(None, "--to", formats=["%Y-%m-%d"]),
) -> None:
    """List recent activities."""
    from .services import activities as service

    found = _run(
        service.list_activities,
        limit=limit,
        activity_type=activity_type,
        start_date=start_date.date() if start_date else None,
        end_date=end_date.date() if end_date else None,
    )
    _print([a.model_dump(exclude_none=True, exclude={"raw"}) for a in found])


@app.command()
def sets(activity_id: int, include_rests: bool = False) -> None:
    """Show the sets of a strength activity."""
    from .services import activities as service

    result = _run(service.get_sets, activity_id, include_rests=include_rests)
    _print(
        {
            "activity_id": result.activity_id,
            "total_volume_kg": result.total_volume_kg,
            "sets": [s.model_dump(exclude_none=True, exclude={"raw"}) for s in result.sets],
        }
    )


class Format(StrEnum):
    fit = "fit"
    tcx = "tcx"
    gpx = "gpx"
    csv = "csv"


@app.command()
def download(activity_id: int, fmt: Format = Format.fit, out: Path | None = None) -> None:
    """Download an activity's file. The fit format arrives as a zip."""
    from .services import activities as service

    data = _run(service.download, activity_id, fmt.value)
    suffix = "zip" if fmt is Format.fit else fmt.value
    name = f"activity_{activity_id}.{suffix}"
    target = _write(out or Path(name), data, name)
    typer.echo(f"Wrote {len(data)} bytes to {target}")


@app.command()
def day(when: datetime = typer.Argument(..., formats=["%Y-%m-%d"])) -> None:
    """Show one day of health data."""
    from .services import health as service

    summary = _run(service.day, when.date())
    _print(summary.model_dump(exclude_none=True, exclude={"raw"}))


@app.command()
def exercises(query: str, limit: int = typer.Option(15, min=1, max=100)) -> None:
    """Search Garmin's exercise catalogue. Works offline and logged out."""
    _print([e._asdict() for e in catalogue.search(query, limit=limit)])


@app.command()
def upload(path: Path) -> None:
    """Upload an activity file (.fit, .gpx or .tcx) to Garmin Connect."""
    from .services import uploads as service

    _print_upload(_run(service.upload_file, path))


@app.command("log-session")
def log_session(spec_file: Path) -> None:
    """Log a completed strength session from a JSON file.

    The file describes the session: a name, a start_time with a timezone, and
    a list of exercises each with their sets. An example:
    https://github.com/happyc0der/garmin-connect-api/blob/main/examples/session.json
    """
    from .models.workouts import StrengthSessionSpec
    from .services import uploads as service

    spec = _run(StrengthSessionSpec.model_validate_json, _read(spec_file))
    _print_upload(_run(service.log_strength_session, spec))


@app.command("encode-session")
def encode_session(spec_file: Path, out: Path = Path("session.fit")) -> None:
    """Build a session's FIT file without uploading it.

    Useful for inspecting what would be sent, or for importing by hand on the
    Garmin Connect website. Needs no login.
    """
    from .fit import encode_strength_session
    from .models.workouts import StrengthSessionSpec

    spec = _run(StrengthSessionSpec.model_validate_json, _read(spec_file))
    target = _write(out, _run(encode_strength_session, spec), "session.fit")
    typer.echo(f"Wrote {target} ({spec.total_sets} sets)")


@app.command("decode-fit")
def decode_fit(path: Path) -> None:
    """Read a strength session out of a FIT file or a Garmin export zip.

    Needs no login.
    """
    from .fit import decode_strength_session

    spec = _run(decode_strength_session, _read_bytes(path))
    _print(spec.model_dump(mode="json", exclude_none=True))


@workout_app.command("list")
def workout_list(limit: int = 20) -> None:
    """List saved workouts."""
    from .services import workouts as service

    found = _run(service.list_workouts, limit=limit)
    _print([w.model_dump(exclude_none=True, exclude={"raw"}) for w in found])


@workout_app.command("create")
def workout_create(spec_file: Path) -> None:
    """Create a strength workout from a JSON spec file."""
    from .models.workouts import StrengthWorkoutSpec
    from .services import workouts as service

    spec = _run(StrengthWorkoutSpec.model_validate_json, _read(spec_file))
    created = _run(service.create_strength, spec)
    _print(created.model_dump(exclude_none=True, exclude={"raw"}))


@workout_app.command("schedule")
def workout_schedule(
    workout_id: int, when: datetime = typer.Argument(..., formats=["%Y-%m-%d"])
) -> None:
    """Put a workout on the calendar."""
    from .services import workouts as service

    _run(service.schedule, workout_id, when.date())
    typer.secho(f"Scheduled workout {workout_id} for {when.date()}.", fg=typer.colors.GREEN)


@workout_app.command("delete")
def workout_delete(workout_id: int) -> None:
    """Delete a saved workout."""
    from .services import workouts as service

    _run(service.delete, workout_id)
    typer.echo(f"Deleted workout {workout_id}.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, reload: bool = False) -> None:
    """Run the HTTP service.

    Localhost only by default. Binding anywhere else needs GARMIN_API_KEY set,
    since the service can write to the Garmin account, and the host name
    clients use listed in GARMIN_API_HOSTS.
    """
    import uvicorn

    _refuse_open_bind(host)
    typer.echo(f"Docs at http://{host}:{port}/docs")
    uvicorn.run("garmin_api.http.app:app", host=host, port=port, reload=reload)


@app.command()
def mcp(
    http: bool = typer.Option(False, help="Serve over HTTP instead of stdio."),
    host: str = typer.Option(
        "127.0.0.1", envvar="FASTMCP_HOST", help="With --http: the address to bind."
    ),
    port: int = typer.Option(8000, envvar="FASTMCP_PORT", help="With --http: the port."),
) -> None:
    """Run the MCP server.

    Speaks stdio by default, which is what Claude Code and Claude Desktop
    expect. Over HTTP it is guarded like `garmin serve`: localhost only unless
    GARMIN_API_KEY is set, and then every request must present that key and
    name a host listed in GARMIN_API_HOSTS.
    """
    from .mcp import create_mcp

    server = create_mcp()
    if not http:
        server.run(transport="stdio", show_banner=False)
        return

    import uvicorn

    _refuse_open_bind(host)
    typer.echo(f"MCP over HTTP at http://{host}:{port}/mcp", err=True)
    # create_mcp's http_app carries the same guard as `garmin serve`.
    uvicorn.run(server.http_app(), host=host, port=port)


def _refuse_open_bind(host: str) -> None:
    """Both servers can write to the account; beyond localhost they need a key.

    Clients must then also use a host name listed in GARMIN_API_HOSTS.
    """
    import os

    if host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("GARMIN_API_KEY"):
        typer.secho(
            "Refusing to bind beyond localhost without GARMIN_API_KEY set.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


def mcp_entry() -> None:
    """``uvx garmin-connect-api``: start the MCP server over stdio.

    MCP clients launch a server by running its package, so the package's own
    command starts the server directly. Everything else, including logging
    in, is under the ``garmin`` command.
    """
    from .mcp import create_mcp

    create_mcp().run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    app()
