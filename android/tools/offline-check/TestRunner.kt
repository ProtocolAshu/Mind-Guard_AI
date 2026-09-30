// OFFLINE VERIFICATION RUNNER — executes @org.junit.Test methods by reflection (see junit-shim/org/junit/Shim.kt).
import java.lang.reflect.InvocationTargetException

fun main(args: Array<String>) {
    var passed = 0
    val failures = mutableListOf<String>()
    for (name in args) {
        val cls = Class.forName(name)
        for (method in cls.declaredMethods.filter { it.isAnnotationPresent(org.junit.Test::class.java) }.sortedBy { it.name }) {
            try {
                method.invoke(cls.getDeclaredConstructor().newInstance())
                passed++
                println("PASS ${cls.simpleName}.${method.name}")
            } catch (e: InvocationTargetException) {
                failures += "${cls.simpleName}.${method.name}: ${e.targetException}"
                println("FAIL ${cls.simpleName}.${method.name}: ${e.targetException}")
            }
        }
    }
    println("\n$passed passed, ${failures.size} failed")
    if (failures.isNotEmpty()) kotlin.system.exitProcess(1)
}
