# TripPilot Web

React frontend for the TripPilot local application. The page presents daily activities, itemized travel costs, evidence disclosures and itinerary changes. Creation, revision, explicit conflict answers and cancellation use the existing same-origin API.

The initial Hangzhou screen is clearly labeled illustrative data. Actual execution replays and live tasks use their own records; no demonstration is counted as a measured result.

The Python launcher serves the bundled application without Node.js. To develop the client:

```sh
npm ci
npm run build:app
```

Run the Python launcher from the repository root afterward. The build updates the bundled page and assets; do not rebuild while a model evaluation is running. API credentials remain in the Python parent environment and are not embedded in client assets.

Typography is self-hosted Noto Sans SC under the SIL OFL. Icons use Phosphor; budget visualization uses React Circular Progressbar. Dependency licenses are retained in `licenses/`. The West Lake atmosphere image was generated for this interface and is decorative, not evidence of a live destination service.
