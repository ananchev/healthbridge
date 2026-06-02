// SyncEngine.swift
// Cursors, payload building, POST to backend with bearer token, error handling.
// NOTE for Claude Code: implement per ios/CLAUDE.md and docs/SPEC.md §5.

import Foundation

struct SyncEngine {
    let settings: Settings
    let health: HealthKitManager

    func syncNow() async throws {
        // TODO(claude-code):
        // 1. Read cursors from settings.
        // 2. Query HealthKit for samples newer than each cursor (Apple Watch source).
        // 3. Build the IngestPayload JSON (UTC ISO-8601). Match models.py exactly.
        // 4. POST to settings.endpoint + "/ingest" with headers:
        //      Authorization: Bearer <token> (from Keychain),
        //      Content-Type: application/json
        // 5. On 2xx: advance cursors to max sent timestamps; persist.
        // 6. On error: surface message; do not advance cursors.
    }
}
