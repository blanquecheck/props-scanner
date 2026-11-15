import { windows, etParts } from "./worker.js";
const cases = [
 ["2026-11-14T21:00:00Z","Sat 4pm EST",["nba"]],
 ["2026-11-14T20:59:00Z","Sat 3:59pm",[]],
 ["2026-11-15T04:59:00Z","Sat 11:59pm",["nba"]],
 ["2026-11-15T05:00:00Z","Sun 12am",[]],
 ["2026-11-15T15:00:00Z","Sun 10am",["nfl"]],
 ["2026-11-15T14:59:00Z","Sun 9:59am",[]],
 ["2026-11-15T22:00:00Z","Sun 5pm",["nba","nfl"]],
 ["2026-11-16T01:59:00Z","Sun 8:59pm",["nba","nfl"]],
 ["2026-11-16T02:00:00Z","Sun 9pm",["nba"]],
 ["2026-11-16T23:30:00Z","Mon 6:30pm",["nba","nfl"]],
 ["2026-11-18T23:30:00Z","Wed 6:30pm",["nba"]],
 ["2026-11-19T23:30:00Z","Thu 6:30pm",["nba","nfl"]],
 ["2026-11-20T15:00:00Z","Fri 10am",[]],
 ["2026-07-12T14:00:00Z","Sun 10am EDT",["nfl"]],
 ["2026-07-13T03:59:00Z","Sun 11:59pm EDT",["nba"]],
];
let fail=0; for (const [iso,l,exp] of cases){const g=windows(new Date(iso));const ok=JSON.stringify(g)===JSON.stringify(exp);if(!ok)fail++;console.log(ok?"PASS":"FAIL",l,iso,JSON.stringify(etParts(new Date(iso))),g);}
process.exit(fail);
