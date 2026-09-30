// OFFLINE VERIFICATION SHIM — not part of the app or the Gradle build.
// Maven Central is unreachable in the build sandbox, so JUnit 4 itself is unavailable. This file provides only the
// subset of org.junit used by android/*/src/test (the @Test annotation and a few assertions) so the same test sources
// can be compiled and executed with plain kotlinc. Gradle builds use the real junit:junit:4.13.2.
package org.junit

@Retention(AnnotationRetention.RUNTIME)
@Target(AnnotationTarget.FUNCTION)
annotation class Test

object Assert {
    @JvmStatic fun assertEquals(expected: Any?, actual: Any?) {
        if (expected != actual) throw AssertionError("expected:<$expected> but was:<$actual>")
    }
    @JvmStatic fun assertEquals(expected: Double, actual: Double, delta: Double) {
        if (kotlin.math.abs(expected - actual) > delta) throw AssertionError("expected:<$expected> but was:<$actual>")
    }
    @JvmStatic fun assertTrue(condition: Boolean) { if (!condition) throw AssertionError("expected true") }
    @JvmStatic fun assertFalse(condition: Boolean) { if (condition) throw AssertionError("expected false") }
    @JvmStatic fun assertNull(value: Any?) { if (value != null) throw AssertionError("expected null but was:<$value>") }
    @JvmStatic fun assertNotNull(value: Any?) { if (value == null) throw AssertionError("expected not null") }
}
