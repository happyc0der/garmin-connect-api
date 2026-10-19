# Security

This package holds tokens that can read and change a Garmin Connect account, so please report
security problems privately rather than in a public issue: use GitHub's
[private vulnerability reporting](https://github.com/happyc0der/garmin-connect-api/security/advisories/new).

What is in scope: anything that could expose stored tokens, let something other than the owner
reach the local HTTP service or the MCP server's write tools, or write to Garmin without the
owner asking.

Tokens live in `~/.garmin-api/default` (or `GARMINTOKENS`), readable by your user only. If you
think they have leaked, run `garmin logout` and then `garmin login` to replace them.
