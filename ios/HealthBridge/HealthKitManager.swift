// HealthKitManager.swift
// HealthKit authorization, queries, stage mapping, and source enumeration.
// NOTE for Claude Code: implement per ios/CLAUDE.md. Read-only authorization.

import Foundation
import HealthKit

final class HealthKitManager {
    let store = HKHealthStore()

    // Types we read.
    var readTypes: Set<HKObjectType> {
        [
            HKCategoryType(.sleepAnalysis),
            HKQuantityType(.heartRateVariabilitySDNN),
            HKQuantityType(.restingHeartRate),
        ]
    }

    func requestAuthorization() async throws {
        guard HKHealthStore.isHealthDataAvailable() else { return }
        try await store.requestAuthorization(toShare: [], read: readTypes)
    }

    // Map HKCategoryValueSleepAnalysis raw enum to the backend's bare strings.
    // TODO(claude-code): implement full mapping.
    //   .asleepCore -> "AsleepCore", .asleepDeep -> "AsleepDeep",
    //   .asleepREM -> "AsleepREM", .asleepUnspecified -> "AsleepUnspecified",
    //   .awake -> "Awake", .inBed -> "InBed".
    func stageString(for value: Int) -> String? {
        return nil
    }

    // TODO(claude-code):
    //  - querySleep(since:) / queryHRV(since:) / queryRHR(since:) returning typed
    //    arrays ready to serialize.
    //  - sourcesWritingSleep() -> [String] for the in-app source picker.
}
