pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "MindGuard"
include(":app", ":core", ":data", ":domain", ":ai", ":monitoring", ":intervention", ":permissions", ":analytics", ":settings")
