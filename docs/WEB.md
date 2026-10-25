# The web app (`web/`)

Next.js 16 (App Router, Turbopack), React 19, TypeScript, no UI framework: hand-written CSS in `app/styles/` (tokens → base → components → layout → desk → agent), light and dark, Atkinson Hyperlegible for readability. Crypto: `@noble/ed25519` (pure JS, so it works on plain-http LAN addresses where WebCrypto is disabled) and `openpgp` (lazy-loaded). Maps: `leaflet` (imported inside an effect, never on the server) over OpenStreetMap or Esri satellite tiles; the boundaries draw even when the tiles cannot load.

```bash
cd web && npm install && npm run dev      # the scripts start it for you
npm run typecheck && npm run build        # production build; LANDCHAIN_WEB_MODE=start to serve it
```

`LANDCHAIN_GATEWAY_URL` (default `http://127.0.0.1:13900`) points the proxy at the gateway; `NEXT_PUBLIC_SITE_URL` sets canonical URLs.

## Routes

| Route | Who | What |
|---|---|---|
| `/`, `/register`, `/register/[...sy]` | public | landing with the map; the register as a map with text, area, status, map-window and near-a-point search; parcel page with boundary, neighbours, overlaps, chain of ownership and each transfer's signed public record |
| `/verify` | public | verify a report (signature + ledger), a transfer record (cleartext signature + attested key + ledger) or a document |
| `/how-it-works` | public | the story in plain words |
| `/agent` | public status, officers for the rest | live graph, runs, quality, ask, how it is built |
| `/sign-in`, `/officials/sign-in`, `/citizen/sign-in`, `/citizen/register` | public | authentication |
| `/officials/**` | officers | queue, applications (+ detail with actions), parcels, transfer requests, register & issue, ledger, nodes |
| `/citizen/**` | citizens | my land, sales and purchases, sell, file (+ report verification), documents, access log, signing key |

## Structure

* `app/(public)`, `app/(officials)/officials`, `app/(citizen)/citizen`: route groups; the latter two wrap their pages in `OfficerGate`/`CitizenGate` + `DashboardShell`.
* `lib/api.ts` browser → `/gw/*` → gateway (`app/gw/[...path]/route.ts`), bearer token; `lib/sse.ts` fetch-based SSE (EventSource cannot send headers); `lib/useApi.ts` data hook with polling; `lib/session.tsx` separate officer and citizen sessions; `lib/did.ts` key generation, signing, key files; `lib/verify.ts` report verification; `lib/types.ts` every gateway shape.
* `components/agent/*` the validator console: `graph-view`, `live` (with the model's investigation as it happens), `investigation` (turns, calls, grounded and refused findings), `runs`, `run-detail` (investigation, explanations with per-sentence support and reflections, verifier, files, trace), `metrics` (including the model's own choices), `ask` (shows the tools it used), `design` (with the agent-loop subgraph).
* `components/map/*`: `land-map` (Leaflet), `register-explorer`, `parcel-map`, `home-map`. `components/parcel/*`: `ownership-chain`, `transfer-records`, `record-downloads`. `components/desk/register-parcel.tsx`: registration with a boundary check on the map. `lib/records.ts`: download and verify transfer records.
* `components/desk/actions.tsx` the role actions; each calls one gateway endpoint.

## The web-platform checklist

| Item | Where |
|---|---|
| Custom 404 | `app/not-found.tsx` |
| `robots.txt`, `sitemap.xml` | `app/robots.ts`, `app/sitemap.ts` (one entry per parcel) |
| Web manifest | `app/manifest.ts` |
| `<title>` + meta description, canonical URL | `lib/metadata.ts` (`pageMetadata`), per page |
| Semantic HTML | landmarks, `h1` per page, tables with captions, `role=tablist`, skip link |
| X card + Open Graph + OG image | `app/opengraph-image.tsx`, `twitter-image.tsx`, `app/og/parcel/route.tsx` (per-parcel) |
| Initial loading state | `app/(public)/(home)/loading.tsx`, `app/(public)/register/(list)/loading.tsx`, the skeletons inside the client gates, and `components/ui/skeleton.tsx`. There is deliberately no loading boundary above the parcel page: one would make Next stream a 200 status, so an unknown parcel would be a soft 404 instead of a real one |
| Image placeholders, broken-image fallback | `components/ui/smart-image.tsx` |
| Search with no results | `NoResults` in `components/ui/states.tsx` (register, applications, parcels, runs) |
| Generic error state | `app/error.tsx`, `app/global-error.tsx`, `ErrorState` for failed requests |
