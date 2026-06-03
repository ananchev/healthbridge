// BackgroundSync.swift — BGTaskScheduler (daily) + HKObserverQuery (best-effort).
// Wire this AFTER manual sync works end-to-end.

import BackgroundTasks
import Foundation
import HealthKit

enum BackgroundSync {
    static let taskIdentifier = "cc.tonio.healthbridge.sync"

    // Call from AppDelegate.application(_:didFinishLaunchingWithOptions:) or
    // App.init() — must be called before app finishes launching.
    static func register(engine: SyncEngine) {
        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: taskIdentifier,
            using: nil
        ) { task in
            guard let appRefreshTask = task as? BGAppRefreshTask else { return }
            Task {
                appRefreshTask.expirationHandler = { appRefreshTask.setTaskCompleted(success: false) }
                do {
                    _ = try await engine.syncNow()
                    appRefreshTask.setTaskCompleted(success: true)
                } catch {
                    appRefreshTask.setTaskCompleted(success: false)
                }
                schedule()
            }
        }
    }

    // Submit the next daily background sync. Call once after launch and after each
    // completed task so iOS always has a pending request.
    static func schedule() {
        let request = BGAppRefreshTaskRequest(identifier: taskIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 6 * 3600)
        try? BGTaskScheduler.shared.submit(request)
    }

    // Enable HKObserverQuery for each type. When HealthKit delivers new samples it
    // wakes the app (best-effort on free provisioning) and we trigger a sync.
    // The completionHandler must be called promptly to tell HealthKit we're done;
    // actual work runs in a detached Task.
    static func enableObservers(store: HKHealthStore, engine: SyncEngine) {
        let types: [HKObjectType] = [
            HKCategoryType(.sleepAnalysis),
            HKQuantityType(.heartRateVariabilitySDNN),
            HKQuantityType(.restingHeartRate),
        ]
        for type in types {
            let query = HKObserverQuery(sampleType: type as! HKSampleType, predicate: nil) { _, completion, error in
                guard error == nil else { completion(); return }
                Task.detached {
                    try? await engine.syncNow()
                }
                completion()
            }
            store.execute(query)
            store.enableBackgroundDelivery(for: type as! HKSampleType, frequency: .immediate) { _, _ in }
        }
    }
}
