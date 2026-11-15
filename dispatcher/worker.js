// props-dispatcher: triggers blanquecheck/props-scanner "scan" workflow via workflow_dispatch
// on a Cloudflare Cron Trigger, only inside the US-Eastern scan windows from cron/props.cron:
//   NBA nightly: every day 16:00-23:59 ET
//   NFL: Sunday 10:00-20:59 ET, Monday + Thursday 18:00-20:59 ET
// The workflow's own `scanner auto` decides which sports/closes to run from the ET clock.
// Secret required: GITHUB_TOKEN (fine-grained PAT, repo blanquecheck/props-scanner, Actions: read/write).

const OWNER = "blanquecheck";
const REPO = "props-scanner";
const WORKFLOW = "scan.yml";
const REF = "main";

export function etParts(date) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour12: false,
    weekday: "short", hour: "2-digit", minute: "2-digit",
  });
  const p = Object.fromEntries(fmt.formatToParts(date).map(x => [x.type, x.value]));
  const dow = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(p.weekday);
  return { dow, hour: Number(p.hour) % 24, minute: Number(p.minute) };
}

export function windows(date) {
  const { dow, hour } = etParts(date);
  const out = [];
  if (hour >= 16 && hour <= 23) out.push("nba");
  if (dow === 0 && hour >= 10 && hour <= 20) out.push("nfl");
  if ((dow === 1 || dow === 4) && hour >= 18 && hour <= 20) out.push("nfl");
  return out;
}

async function gh(env, method, path, body) {
  const r = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}${path}`, {
    method,
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "props-dispatcher-worker",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r;
}

async function activeRuns(env) {
  let n = 0;
  for (const status of ["in_progress", "queued"]) {
    const r = await gh(env, "GET", `/actions/workflows/${WORKFLOW}/runs?status=${status}&per_page=5`);
    if (!r.ok) throw new Error(`runs list ${status} HTTP ${r.status}`);
    n += (await r.json()).total_count || 0;
  }
  return n;
}

export async function tick(env, now = new Date(), { ignoreWindow = false } = {}) {
  const et = etParts(now);
  const w = windows(now);
  const base = { at: now.toISOString(), et, windows: w };
  if (!w.length && !ignoreWindow) return { ...base, action: "skip", reason: "outside scan windows" };
  if (!env.GITHUB_TOKEN) return { ...base, action: "skip", reason: "GITHUB_TOKEN secret not set" };
  const active = await activeRuns(env);
  if (active > 0) return { ...base, action: "skip", reason: `${active} run(s) already queued/in progress` };
  const r = await gh(env, "POST", `/actions/workflows/${WORKFLOW}/dispatches`, { ref: REF, inputs: { force: "" } });
  if (r.status !== 204) return { ...base, action: "error", reason: `dispatch HTTP ${r.status}: ${(await r.text()).slice(0, 200)}` };
  return { ...base, action: "dispatched" };
}

export default {
  async scheduled(event, env, ctx) {
    try {
      console.log(JSON.stringify(await tick(env, new Date(event.scheduledTime))));
    } catch (e) {
      console.log(JSON.stringify({ action: "error", reason: String(e) }));
    }
  },
  async fetch(request, env) {
    // Read-only status page; never dispatches and never reveals the token.
    const now = new Date();
    return Response.json({
      worker: "props-dispatcher",
      now: now.toISOString(),
      et: etParts(now),
      in_window: windows(now),
      token_configured: Boolean(env.GITHUB_TOKEN),
      target: `${OWNER}/${REPO} ${WORKFLOW}@${REF}`,
    });
  },
};
