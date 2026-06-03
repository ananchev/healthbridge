# Xcode Project Setup — HealthBridge iOS

All Swift source files are in `ios/HealthBridge/` and are complete. This document
covers the one-time Xcode project creation and manual configuration that cannot be
scripted.

## 1. Create the project

Open Xcode → **File → New → Project → App**

| Field | Value |
|---|---|
| Product Name | `HealthBridge` |
| Bundle Identifier | `cc.tonio.healthbridge` |
| Interface | SwiftUI |
| Language | Swift |
| Include Tests | unchecked (tests are added manually below) |

**Save location:** `ios/` inside the repo root. Xcode will create
`ios/HealthBridge.xcodeproj` and `ios/HealthBridge/` with default stubs.

## 2. Replace Xcode's generated stubs

Xcode creates its own `ContentView.swift` and `HealthBridgeApp.swift`. Delete them
(Move to Trash). The repo already has the real source files at `ios/HealthBridge/`.

In Xcode's Project navigator, right-click the **HealthBridge** group →
**Add Files to "HealthBridge"…** → select all `.swift` files in `ios/HealthBridge/`.
Make sure "Add to target: HealthBridge" is checked.

Files to add:

```
HealthBridgeApp.swift
ContentView.swift
HealthKitManager.swift
SyncEngine.swift
BackgroundSync.swift
Keychain.swift
Settings.swift
```

## 3. Capabilities (Signing & Capabilities tab → target, not project)

- Set **Team** to your personal free Apple account.
- **+ Capability → HealthKit**
- **+ Capability → Background Modes** → check:
  - Background fetch
  - Background processing

## 4. Info.plist entries

In Xcode 15+, open the **Info** tab of the target (or edit `Info.plist` as source).
Add:

```xml
<key>NSHealthShareUsageDescription</key>
<string>HealthBridge reads your sleep, HRV, and resting heart rate to sync them to your private server.</string>

<key>BGTaskSchedulerPermittedIdentifiers</key>
<array>
    <string>cc.tonio.healthbridge.sync</string>
</array>
```

No `NSHealthUpdateUsageDescription` — the app only reads HealthKit.

## 5. Run on device

- Plug in your iPhone and select it as the run destination.
- Hit **Run** (⌘R). iOS will prompt for HealthKit read permission on first launch.
- Free-account signing cert expires every **7 days** — re-run from Xcode to refresh.

## 6. First-use configuration in the app

On the **Server** section:
- Endpoint: `https://healthbridge.example.com` (pre-filled default)
- Bearer token: paste the value of `HEALTHBRIDGE_TOKEN` from `.env.dev`

On the **Apple Watch source** section:
- Tap **Pick source…** — the app queries HealthKit for all sources that have written
  sleep data and lists them.
- Select your Apple Watch (name contains a **non-breaking space** U+00A0, e.g.
  `Apple Watch van Owner`). The app stores the name verbatim.

## 7. End-to-end test (manual sync)

With NPM flipped to the laptop (`./scripts/dev/npm-flip.sh laptop 192.168.2.5`) and
uvicorn running:

1. Tap **Sync now**.
2. Watch uvicorn console — expect a POST to `/ingest` with a 200 response.
3. Check the UI: sleep/HRV/RHR counts update and "Last sync" shows the current time.
4. Tap **Sync now** again — counts should be 0 (cursors advanced, nothing new).

Restore NPM when done: `./scripts/dev/npm-flip.sh prod`.

## 8. Testing strategy

| Layer | How |
|---|---|
| Stage mapping (`stageString`) | XCTest unit test (pure function) |
| Payload JSON encoding | XCTest unit test (no HealthKit/network needed) |
| Keychain set/get/delete | XCTest on simulator |
| HealthKit auth prompt | Visual — run on device |
| Source picker lists Watch | Visual — run on device |
| Sync → backend rows | Manual — flip NPM, tap Sync, check uvicorn log |
| Cursor advancement | Manual — sync twice, second run sends 0 new rows |
| 401 on missing token | Manual — clear token, tap Sync, UI shows error |

For compiler errors or runtime issues, paste the Xcode console output or error
message back to Claude Code for diagnosis.

## 9. Free provisioning caveats

- Background delivery via `HKObserverQuery` is throttled heavily on free provisioning.
  Manual sync and the daily `BGTaskScheduler` task are the reliable paths.
- The BGTaskScheduler daily task will not fire unless the app has been launched at
  least once since the last re-sign.
- To test BGTaskScheduler: pause in the debugger and run
  `e -l objc -- (void)[[BGTaskScheduler sharedScheduler] _simulateLaunchForTaskWithIdentifier:@"cc.tonio.healthbridge.sync"]`
  in the Xcode console.
