#include <cassert>
#include <cstring>

#include "../Three_Gate_Data_Collection/ClassificationSequence.h"

void testSuccessfulSequenceUsesExactDeadlines() {
  ClassificationSequence sequence;

  const auto started = sequence.start(42, "high", 100);
  assert(started.action == ClassificationSequence::Action::kMoveSorterToTarget);
  assert(started.angle == 55);
  assert(sequence.busy());

  assert(sequence.tick(599).action == ClassificationSequence::Action::kNone);
  assert(sequence.tick(600).action == ClassificationSequence::Action::kReleaseGate3);
  assert(sequence.tick(1599).action == ClassificationSequence::Action::kNone);
  assert(sequence.tick(1600).action == ClassificationSequence::Action::kHomeAll);
  assert(sequence.tick(2099).action == ClassificationSequence::Action::kNone);

  const auto completed = sequence.tick(2100);
  assert(completed.action == ClassificationSequence::Action::kCompleted);
  assert(completed.commandId == 42);
  assert(!sequence.busy());
}

void assertTargetAngle(const char* code, int expectedAngle) {
  ClassificationSequence sequence;
  const auto started = sequence.start(1, code, 0);
  assert(started.action == ClassificationSequence::Action::kMoveSorterToTarget);
  assert(started.angle == expectedAngle);
}

void testCanonicalCodesAndAliasesUseRequiredAngles() {
  assertTargetAngle("high", 55);
  assertTargetAngle("medium", 70);
  assertTargetAngle("low", 100);
  assertTargetAngle("processing", 115);
  assertTargetAngle("high_medium", 55);
  assertTargetAngle("discard", 70);
}

void testInvalidCodeNeverReleasesGate3() {
  ClassificationSequence sequence;
  const auto result = sequence.start(7, "unknown", 0);
  assert(result.action == ClassificationSequence::Action::kFailed);
  assert(std::strcmp(result.reason, "invalid_classification_code") == 0);
  assert(!sequence.busy());
  assert(sequence.tick(10000).action == ClassificationSequence::Action::kNone);
}

void testTimeoutHomesBeforeReportingFailure() {
  ClassificationSequence sequence;
  assert(
      sequence.start(8, "medium", 100).action ==
      ClassificationSequence::Action::kMoveSorterToTarget);

  const auto homing = sequence.tick(5100);
  assert(homing.action == ClassificationSequence::Action::kHomeAll);
  assert(homing.angle == 85);
  assert(sequence.busy());

  const auto failed = sequence.tick(5600);
  assert(failed.action == ClassificationSequence::Action::kFailed);
  assert(std::strcmp(failed.reason, "classifier_timeout") == 0);
  assert(!sequence.busy());
}

void testDuplicateCommandNeverStartsMotionAgain() {
  ClassificationSequence sequence;
  assert(
      sequence.start(9, "processing", 0).action ==
      ClassificationSequence::Action::kMoveSorterToTarget);
  assert(sequence.start(9, "processing", 1).action == ClassificationSequence::Action::kIgnored);
  assert(sequence.tick(500).action == ClassificationSequence::Action::kReleaseGate3);
  assert(sequence.tick(1500).action == ClassificationSequence::Action::kHomeAll);
  assert(sequence.tick(2000).action == ClassificationSequence::Action::kCompleted);
  assert(sequence.start(9, "processing", 3000).action == ClassificationSequence::Action::kIgnored);
}

void testBootCapabilityWaitsForSorterHome() {
  assert(!ClassificationSequence::bootHomeReady(200, 0));
  assert(!ClassificationSequence::bootHomeReady(499, 0));
  assert(ClassificationSequence::bootHomeReady(500, 0));
}

int main() {
  testSuccessfulSequenceUsesExactDeadlines();
  testCanonicalCodesAndAliasesUseRequiredAngles();
  testInvalidCodeNeverReleasesGate3();
  testTimeoutHomesBeforeReportingFailure();
  testDuplicateCommandNeverStartsMotionAgain();
  testBootCapabilityWaitsForSorterHome();
}
