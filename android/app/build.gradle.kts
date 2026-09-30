plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "dev.mindguard.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "dev.mindguard.app"
        minSdk = 29 // UsageEvents ACTIVITY_RESUMED/PAUSED and AppOpsManager.unsafeCheckOpNoThrow
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    sourceSets["main"].java.srcDirs("src/main/kotlin")
}

kotlin { jvmToolchain(17) }

dependencies {
    implementation(project(":core"))
    implementation(project(":data"))
    implementation(project(":domain"))
    implementation(project(":ai"))
    implementation(project(":monitoring"))
    implementation(project(":intervention"))
    implementation(project(":permissions"))
    implementation(project(":analytics"))
    implementation(project(":settings"))
    implementation(libs.kotlinx.coroutines.android)
    testImplementation(libs.junit)
}
