"""A read/write API for Garmin Connect.

Three faces over one core:

* the library -- :mod:`garmin_api.services`, plain typed functions;
* the HTTP service -- :mod:`garmin_api.http`, FastAPI over those functions;
* the MCP server -- :mod:`garmin_api.mcp`, generated from the HTTP app.

Everything talks to Garmin through :mod:`garmin_api.auth`, which owns the one
authenticated client and the token store.
"""

__version__ = "0.2.6"
