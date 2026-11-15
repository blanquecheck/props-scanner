# props-dispatcher (Cloudflare Worker)

Runs the `scan` workflow without the Mac. GitHub's own `schedule:` cron is heavily throttled
for this repo (runs fire every 1.5–4 hours instead of every 5 minutes), but `workflow_dispatch`
is not, so a Cloudflare Worker cron fires a dispatch every 3 minutes.

- Worker: `props-dispatcher` on the jreynolds889 Cloudflare account (free plan)
- Status page (read-only, never dispatches): https://props-dispatcher.jreynolds889.workers.dev/
- Cron (UTC): `*/3 14-23,0-4 * * *` — covers 9/10am–11:59pm ET; the code then only dispatches inside
  the windows from `cron/props.cron` (US-Eastern, DST-aware):
  - NBA: every day 4:00–11:59 PM ET
  - NFL: Sunday 10:00 AM–8:59 PM ET, Monday + Thursday 6:00–8:59 PM ET
- Guard: skips if a `scan` run is already queued or in progress (the workflow's `concurrency`
  group is a second guard).
- `scanner auto` inside the workflow still decides which sports/closes to run.

Secret: `GITHUB_TOKEN` = fine-grained PAT, resource owner `blanquecheck`, only repo
`blanquecheck/props-scanner`, permission **Actions: Read and write** (Metadata: read is automatic).

```
npx wrangler secret put GITHUB_TOKEN --name props-dispatcher
```

Logs: Cloudflare dashboard → Workers → props-dispatcher → Logs (each tick logs one JSON line:
`dispatched`, or `skip` with a reason).

`test.mjs` checks the window logic: `node test.mjs`.
