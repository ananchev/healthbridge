# iOS Helper App — Build Guide (HealthBridge)

## Reality check for Claude Code

You can WRITE all the Swift source, Info.plist entries, and entitlements, and lay
out the Xcode project structure. You CANNOT build, sign, or run it — that requires
Xcode on Owner's Mac with his free Apple Developer account. So your job here:

1. Produce complete, correct Swift files that compile once opened in Xcode.
2. Document the exact manual Xcode steps Owner must do (capabilities, signing).
3. Keep the app minimal and dependency-free (pure SwiftUI + HealthKit + URLSession).

## Manual steps Owner does in Xcode (document these in README)

1. New Xcode project → App → SwiftUI → name "HealthBridge", bundle id
   `cc.tonio.healthbridge`, team = his personal (free) team.
2. Signing & Capabilities → add **HealthKit** capability.
3. Signing & Capabilities → add **Background Modes** → check "Background fetch"
   and "Background processing" (for HKObserverQuery + BGTaskScheduler).
4. Info.plist → add:
   - `NSHealthShareUsageDescription` = "HealthBridge reads your sleep, HRV, and
     resting heart rate to sync them to your private server."
   - (No NSHealthUpdateUsageDescription — we only read.)
5. Register a BGTaskScheduler identifier (e.g. `cc.tonio.healthbridge.sync`) in
   Info.plist under `BGTaskSchedulerPermittedIdentifiers`.
6. Free-account caveat: 7-day signing cert. Re-run from Xcode weekly to refresh.
   Background delivery may be deprioritized by iOS on free provisioning — manual
   + scheduled sync are the reliable paths; observers are best-effort.

## Data types to read (must match backend wire format)

- `HKCategoryType(.sleepAnalysis)` → map HKCategoryValueSleepAnalysis enum back to
  the bare stage strings the backend expects: AsleepCore/AsleepDeep/AsleepREM/
  AsleepUnspecified/Awake/InBed. (HealthKit gives integer enum values; map them.)
- `HKQuantityType(.heartRateVariabilitySDNN)` → value in milliseconds.
- `HKQuantityType(.restingHeartRate)` → value in count/min (bpm).

## Source filtering

Sleep: filter to the Apple Watch source. Match on `HKSource.name` — but remember
the NON-BREAKING SPACE. Better: let the user pick/confirm the source in-app from
the list of sources that have written sleep data, store the chosen source name.
Do NOT hardcode "Apple Watch" with a regular space; it will not match.

## Sync cursors

Persist per-type the max sample end/startDate successfully sent (UserDefaults).
Query HealthKit with an HKQuery predicate `startDate > cursor`. Advance cursor only
after a 2xx from the backend.

## Wire format (must match backend/healthbridge/models.py)

POST JSON to `${endpoint}/ingest`, all timestamps UTC ISO-8601. See docs/SPEC.md §5.
Send the bearer token on every request:
  Authorization: Bearer <HEALTHBRIDGE_TOKEN>  (from Keychain).

The endpoint is the STABLE public hostname `https://healthbridge.example.com` in BOTH
dev and prod — the only thing that changes is NPM's upstream (flipped to the laptop
in dev, a Docker host in prod). So the app is configured once and never needs to
know whether it's hitting dev or prod. See deploy/NETWORKING.md.

## File layout (create these)

- `HealthBridgeApp.swift`   — @main app entry.
- `ContentView.swift`       — single-screen UI (status, endpoint, token, buttons).
- `HealthKitManager.swift`  — auth, queries, stage mapping, source listing.
- `SyncEngine.swift`        — cursors, payload building, POST, error handling.
- `BackgroundSync.swift`    — BGTaskScheduler + HKObserverQuery wiring.
- `Keychain.swift`          — minimal Keychain wrapper for the bearer token.
- `Settings.swift`          — UserDefaults-backed config (endpoint, source, cursors).

Implement manual sync first (ContentView button → SyncEngine.syncNow()). Get the
end-to-end path green before wiring BackgroundSync.
