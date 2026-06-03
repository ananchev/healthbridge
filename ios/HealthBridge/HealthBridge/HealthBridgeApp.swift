// HealthBridgeApp.swift — @main entry point.

import SwiftUI

@main
struct HealthBridgeApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(model)
                .task {
                    await model.requestAuthorizationIfNeeded()
                }
        }
    }
}

// MARK: - AppModel

@MainActor
@Observable
final class AppModel {
    var lastSync: Date?
    var lastError: String?
    var sleepCount: Int = 0
    var hrvCount: Int = 0
    var rhrCount: Int = 0
    var autoSyncEnabled: Bool = false

    @ObservationIgnored let settings = Settings.shared
    @ObservationIgnored let health = HealthKitManager()
    @ObservationIgnored private let engine: SyncEngine

    init() {
        self.engine = SyncEngine(settings: settings, health: health)
    }

    func requestAuthorizationIfNeeded() async {
        do {
            try await health.requestAuthorization()
        } catch {
            lastError = "HealthKit auth failed: \(error.localizedDescription)"
        }
    }

    func syncNow() async {
        lastError = nil
        do {
            let result = try await engine.syncNow()
            sleepCount = result.sleepWritten
            hrvCount   = result.hrvWritten
            rhrCount   = result.rhrWritten
            lastSync   = Date()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func handleAutoSyncToggle(_ enabled: Bool) {
        if enabled {
            BackgroundSync.register(engine: engine)
            BackgroundSync.schedule()
            BackgroundSync.enableObservers(store: health.store, engine: engine)
        }
        // Disabling: BGTaskScheduler doesn't offer individual cancellation on free
        // provisioning; observers stay registered but are best-effort anyway.
    }
}
