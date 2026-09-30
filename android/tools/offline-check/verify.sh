#!/usr/bin/env bash
# Offline verification used where Gradle cannot download dependencies (see android/README.md):
#   1. type-check every production Kotlin source against the real Android SDK jar and kotlinx-coroutines;
#   2. compile and run the domain unit tests on the JVM through the minimal JUnit shim.
# Required: KOTLINC (kotlinc 2.0+), ANDROID_JAR (platforms/android-34/android.jar), COROUTINES_JAR (kotlinx-coroutines-core-jvm 1.10.x).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${KOTLINC:?set KOTLINC}" "${ANDROID_JAR:?set ANDROID_JAR}" "${COROUTINES_JAR:?set COROUTINES_JAR}"
OUT="$(mktemp -d)"
cd "$ROOT"
SOURCES=$(find . -path ./tools -prune -o -path "*/src/main/kotlin/*.kt" -print)
echo "[1/2] compiling $(echo "$SOURCES" | wc -l) production sources against $(basename "$ANDROID_JAR")"
"$KOTLINC" -no-jdk -jvm-target 17 -Werror -classpath "$ANDROID_JAR:$COROUTINES_JAR" -d "$OUT/android" $SOURCES
echo "      $(find "$OUT/android" -name '*.class' | wc -l) classes"
echo "[2/2] domain unit tests"
"$KOTLINC" -jvm-target 17 -d "$OUT/tests" domain/src/main/kotlin domain/src/test/kotlin tools/offline-check/junit-shim tools/offline-check/TestRunner.kt
STDLIB="$(dirname "$(dirname "$(readlink -f "$KOTLINC")")")/lib/kotlin-stdlib.jar"
TESTS=$(cd domain/src/test/kotlin && find . -name "*Test.kt" | sed 's#^\./##; s#\.kt$##; s#/#.#g')
java -cp "$OUT/tests:$STDLIB" TestRunnerKt $TESTS
