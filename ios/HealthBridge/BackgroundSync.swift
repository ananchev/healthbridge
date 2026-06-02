// BackgroundSync.swift
// BGTaskScheduler (daily) + HKObserverQuery (best-effort real-time) wiring.
// NOTE for Claude Code: implement AFTER manual sync works end-to-end.

import BackgroundTasks
import Foundation
import HealthKit

enum BackgroundSync {
    static let taskIdentifier = "cc.tonio.healthbridge.sync"

    // TODO(claude-code):
    //  - register(): BGTaskScheduler.shared.register(forTaskWithIdentifier:)
    //  - schedule(): submit a BGAppRefreshTask ~daily.
    //  - enableObservers(): HKObserverQuery per type with
    //    store.enableBackgroundDelivery(for:frequency:). On callback, run SyncEngine.
    //  Remember: free provisioning may throttle background delivery heavily.
}
