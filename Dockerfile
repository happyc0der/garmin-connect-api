# The MCP server over stdio, built from this checkout. Directories such as
# Glama build it to inspect the tools; no Garmin login is needed for that.
#
# Tokens live in /tokens. Mount an existing directory there (Docker would
# create a missing one as root) and run as your own user, so refreshed tokens
# stay yours:
#   mkdir -p ~/.garmin-api/default
#   docker build -t garmin-connect-api .
#   docker run -it --rm --user "$(id -u):$(id -g)" \
#     -v ~/.garmin-api/default:/tokens garmin-connect-api garmin login
#   docker run -i --rm --user "$(id -u):$(id -g)" \
#     -v ~/.garmin-api/default:/tokens garmin-connect-api
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GARMINTOKENS=/tokens

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

CMD ["garmin-connect-api"]
