# Privacy: usage counting and feedback

FireSim's frontend can count how often it is used and lets users send feedback. Both are designed
so that nothing about a fire, a place or an incident leaves the browser. Code:
`frontend/src/telemetry/telemetry.ts` (settings, allowed fields, links) and
`frontend/src/components/FeedbackDialog.tsx`; the same design as Pyra's `config/telemetry.js`.

## Usage counting

Off unless the build turns it on. The build setting `VITE_FIRESIM_TELEMETRY` picks one provider:

| Setting | What happens |
|---|---|
| `none` (default; also any unknown value, or a provider missing its token/endpoint) | Nothing is sent anywhere and no third-party script is loaded. |
| `cloudflare` + `VITE_FIRESIM_TELEMETRY_TOKEN` | Loads Cloudflare Web Analytics' cookieless beacon (`static.cloudflareinsights.com/beacon.min.js`) with the site token. No cookies, no fingerprinting; Cloudflare counts page views and Web Vitals. FireSim sends nothing of its own. |
| `post` + `VITE_FIRESIM_TELEMETRY_ENDPOINT` (https only) | One `navigator.sendBeacon()` per page load, and one per feedback rating, to that endpoint (a counter the operator runs). |

A `post` record carries these fields and no others (`ALLOWED_KEYS`; anything else is dropped
before sending):

| Field | Value |
|---|---|
| `type` | `pageview` or `rating` |
| `page` | the tab: `simulation`, `eoc` or `about` (page views are recorded as `simulation`, the tab the app opens on) |
| `build` | the first 7 characters of the commit the bundle was built from |
| `viewport` | `phone` (< 768 px) or `desktop` |
| `exercise` | whether exercise mode is on |
| `session` | a random 16-hex id kept in `sessionStorage` for this tab only (gone when the tab closes) |
| `rating` | 1-5, rating records only |

**Never sent:** ignition points, perimeters, coordinates, run IDs, incident or period names,
weather, fuel or any setup value, saved-run passphrases, and nothing the user types.

## Feedback

The top-bar **Feedback** button opens a dialog: "Was this useful today?" 1-5, an optional note,
and **Open a GitHub issue** (a new issue on `Tphambolio/wildfire-simulator`, label `feedback`,
prefilled with the tab, build, exercise-mode on/off, the rating and the note). An **Email**
option appears only when the build sets `VITE_FIRESIM_FEEDBACK_EMAIL`. Nothing is posted until
the user submits the issue or sends the e-mail themselves. The rating is counted only with the
`post` provider; otherwise the dialog says so and suggests opening an issue.

## Turning it on

Set the variables in the Vercel project (Settings → Environment Variables) and redeploy:
`VITE_FIRESIM_TELEMETRY=cloudflare` and `VITE_FIRESIM_TELEMETRY_TOKEN=<site token>` (a new
Cloudflare Web Analytics site for the FireSim domain; Pyra's token belongs to Pyra's site).
