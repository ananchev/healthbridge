// HealthKitManager.swift — authorization, queries, stage mapping, source enumeration.

import Foundation
import HealthKit

final class HealthKitManager {
    let store = HKHealthStore()

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

    // MARK: - Stage mapping

    // Maps HKCategoryValueSleepAnalysis raw Int to the bare stage strings the backend expects.
    // The prefix "HKCategoryValueSleepAnalysis" is stripped; we send just e.g. "AsleepCore".
    func stageString(for value: Int) -> String? {
        guard let v = HKCategoryValueSleepAnalysis(rawValue: value) else { return nil }
        switch v {
        case .asleepCore:        return "AsleepCore"
        case .asleepDeep:        return "AsleepDeep"
        case .asleepREM:         return "AsleepREM"
        case .asleepUnspecified: return "AsleepUnspecified"
        case .awake:             return "Awake"
        case .inBed:             return "InBed"
        @unknown default:        return nil
        }
    }

    // MARK: - Source enumeration

    // Returns the distinct source names that have written sleep samples.
    // Use this to populate the source picker so the user can confirm their Apple Watch.
    func sourcesWritingSleep() async throws -> [String] {
        let type = HKCategoryType(.sleepAnalysis)
        let sources = try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Set<HKSource>, Error>) in
            let query = HKSourceQuery(sampleType: type, samplePredicate: nil) { _, sources, error in
                if let error { cont.resume(throwing: error); return }
                cont.resume(returning: sources ?? [])
            }
            store.execute(query)
        }
        return sources.map(\.name).sorted()
    }

    // MARK: - Queries

    struct SleepRecord {
        let start: Date
        let end: Date
        let stage: String
        let sourceName: String
        let sourceVersion: String?
    }

    struct HRVRecord {
        let timestamp: Date
        let valueMs: Double
        let sourceName: String
    }

    struct RHRRecord {
        let date: Date      // calendar day (will be formatted yyyy-MM-dd)
        let valueBpm: Double
        let sourceName: String
    }

    // Queries sleep samples from `since` onwards, filtered to the chosen source.
    // Pass nil for `since` to get all history (used for first-sync bootstrap).
    func querySleep(since cursor: Date?, sourceName: String) async throws -> [SleepRecord] {
        let type = HKCategoryType(.sleepAnalysis)
        let predicate = cursor.map {
            HKQuery.predicateForSamples(withStart: $0, end: nil, options: .strictStartDate)
        }
        let samples = try await fetchCategorySamples(type: type, predicate: predicate)
        return samples.compactMap { sample in
            guard sample.sourceRevision.source.name == sourceName,
                  let stage = stageString(for: sample.value)
            else { return nil }
            return SleepRecord(
                start: sample.startDate,
                end: sample.endDate,
                stage: stage,
                sourceName: sample.sourceRevision.source.name,
                sourceVersion: sample.sourceRevision.version
            )
        }
    }

    func queryHRV(since cursor: Date?, sourceName: String) async throws -> [HRVRecord] {
        let type = HKQuantityType(.heartRateVariabilitySDNN)
        let predicate = cursor.map {
            HKQuery.predicateForSamples(withStart: $0, end: nil, options: .strictStartDate)
        }
        let samples = try await fetchQuantitySamples(type: type, predicate: predicate)
        return samples
            .filter { $0.sourceRevision.source.name == sourceName }
            .map {
                HRVRecord(
                    timestamp: $0.startDate,
                    valueMs: $0.quantity.doubleValue(for: .init(from: "ms")),
                    sourceName: $0.sourceRevision.source.name
                )
            }
    }

    func queryRHR(since cursor: Date?, sourceName: String) async throws -> [RHRRecord] {
        let type = HKQuantityType(.restingHeartRate)
        let predicate = cursor.map {
            HKQuery.predicateForSamples(withStart: $0, end: nil, options: .strictStartDate)
        }
        let samples = try await fetchQuantitySamples(type: type, predicate: predicate)
        return samples
            .filter { $0.sourceRevision.source.name == sourceName }
            .map {
                RHRRecord(
                    date: $0.startDate,
                    valueBpm: $0.quantity.doubleValue(for: HKUnit(from: "count/min")),
                    sourceName: $0.sourceRevision.source.name
                )
            }
    }

    // MARK: - Private helpers

    private func fetchCategorySamples(
        type: HKCategoryType,
        predicate: NSPredicate?
    ) async throws -> [HKCategorySample] {
        try await withCheckedThrowingContinuation { cont in
            let query = HKSampleQuery(
                sampleType: type,
                predicate: predicate,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)]
            ) { _, samples, error in
                if let error { cont.resume(throwing: error); return }
                cont.resume(returning: (samples as? [HKCategorySample]) ?? [])
            }
            store.execute(query)
        }
    }

    private func fetchQuantitySamples(
        type: HKQuantityType,
        predicate: NSPredicate?
    ) async throws -> [HKQuantitySample] {
        try await withCheckedThrowingContinuation { cont in
            let query = HKSampleQuery(
                sampleType: type,
                predicate: predicate,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)]
            ) { _, samples, error in
                if let error { cont.resume(throwing: error); return }
                cont.resume(returning: (samples as? [HKQuantitySample]) ?? [])
            }
            store.execute(query)
        }
    }
}
