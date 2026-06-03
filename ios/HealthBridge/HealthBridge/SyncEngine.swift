// SyncEngine.swift — cursors, payload building, POST to backend, cursor advancement.

import Foundation

struct SyncResult {
    var sleepWritten: Int = 0
    var hrvWritten: Int = 0
    var rhrWritten: Int = 0
    var nightsRecomputed: [String] = []
}

enum SyncError: LocalizedError {
    case noToken
    case noSource
    case httpError(Int, String)
    case badResponse

    var errorDescription: String? {
        switch self {
        case .noToken:          return "No bearer token configured."
        case .noSource:         return "No Apple Watch source selected."
        case .httpError(let code, let body): return "HTTP \(code): \(body)"
        case .badResponse:      return "Unexpected response from server."
        }
    }
}

final class SyncEngine {
    let settings: Settings
    let health: HealthKitManager
    private let iso8601: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    init(settings: Settings, health: HealthKitManager) {
        self.settings = settings
        self.health = health
    }

    func syncNow() async throws -> SyncResult {
        guard let token = settings.bearerToken, !token.isEmpty else { throw SyncError.noToken }
        guard let source = settings.sleepSourceName, !source.isEmpty else { throw SyncError.noSource }

        async let sleepSamples = health.querySleep(since: settings.sleepCursor, sourceName: source)
        async let hrvSamples   = health.queryHRV(since: settings.hrvCursor, sourceName: source)
        async let rhrSamples   = health.queryRHR(since: settings.rhrCursor, sourceName: source)

        let (sleep, hrv, rhr) = try await (sleepSamples, hrvSamples, rhrSamples)

        // Nothing new — skip the network round-trip.
        if sleep.isEmpty && hrv.isEmpty && rhr.isEmpty { return SyncResult() }

        let payload = buildPayload(sleep: sleep, hrv: hrv, rhr: rhr)
        let result = try await post(payload: payload, token: token)

        // Advance cursors only after confirmed 2xx.
        if let lastSleep = sleep.last { settings.sleepCursor = lastSleep.end }
        if let lastHRV   = hrv.last   { settings.hrvCursor   = lastHRV.timestamp }
        if let lastRHR   = rhr.last   { settings.rhrCursor   = lastRHR.date }

        return result
    }

    // MARK: - Payload builder

    private func buildPayload(
        sleep: [HealthKitManager.SleepRecord],
        hrv: [HealthKitManager.HRVRecord],
        rhr: [HealthKitManager.RHRRecord]
    ) -> [String: Any] {
        let sleepJSON: [[String: Any]] = sleep.map { s in
            var d: [String: Any] = [
                "start":  iso8601.string(from: s.start),
                "end":    iso8601.string(from: s.end),
                "stage":  s.stage,
                "source": s.sourceName,
            ]
            if let v = s.sourceVersion { d["source_version"] = v }
            return d
        }
        let hrvJSON: [[String: Any]] = hrv.map { h in [
            "timestamp": iso8601.string(from: h.timestamp),
            "value_ms":  h.valueMs,
            "source":    h.sourceName,
        ]}
        let rhrJSON: [[String: Any]] = rhr.map { r in [
            "date":      dateFormatter.string(from: r.date),
            "value_bpm": r.valueBpm,
            "source":    r.sourceName,
        ]}
        return [
            "device_id":  settings.deviceID,
            "synced_at":  iso8601.string(from: Date()),
            "data": [
                "sleep": sleepJSON,
                "hrv":   hrvJSON,
                "rhr":   rhrJSON,
            ],
        ]
    }

    // MARK: - Network

    private func post(payload: [String: Any], token: String) async throws -> SyncResult {
        let url = settings.endpoint.appendingPathComponent("ingest")
        var request = URLRequest(url: url, timeoutInterval: 30)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw SyncError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw SyncError.httpError(http.statusCode, body)
        }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return SyncResult()
        }
        return SyncResult(
            sleepWritten:    json["sleep_written"]     as? Int    ?? 0,
            hrvWritten:      json["hrv_written"]       as? Int    ?? 0,
            rhrWritten:      json["rhr_written"]       as? Int    ?? 0,
            nightsRecomputed: json["nights_recomputed"] as? [String] ?? []
        )
    }
}
