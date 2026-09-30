package dev.mindguard.domain

/** Subset of backend/app/policies/catalog.py needed on the device. Essential apps are never restricted. */
object AppCatalog {
    private val categories: Map<String, AppCategory> = mapOf(
        "com.instagram.android" to AppCategory.SOCIAL_MEDIA,
        "com.facebook.katana" to AppCategory.SOCIAL_MEDIA,
        "com.snapchat.android" to AppCategory.SOCIAL_MEDIA,
        "com.twitter.android" to AppCategory.SOCIAL_MEDIA,
        "com.reddit.frontpage" to AppCategory.SOCIAL_MEDIA,
        "com.linkedin.android" to AppCategory.SOCIAL_MEDIA,
        "com.google.android.youtube" to AppCategory.VIDEO,
        "com.zhiliaoapp.musically" to AppCategory.VIDEO,
        "com.netflix.mediaclient" to AppCategory.VIDEO,
        "com.whatsapp" to AppCategory.MESSAGING,
        "org.telegram.messenger" to AppCategory.MESSAGING,
        "com.android.chrome" to AppCategory.BROWSER,
        "com.google.android.dialer" to AppCategory.ESSENTIAL,
        "com.android.dialer" to AppCategory.ESSENTIAL,
        "com.google.android.apps.messaging" to AppCategory.ESSENTIAL,
        "com.android.mms" to AppCategory.ESSENTIAL,
        "com.google.android.apps.maps" to AppCategory.ESSENTIAL,
        "com.google.android.deskclock" to AppCategory.ESSENTIAL,
        "com.google.android.apps.nbu.paisa.user" to AppCategory.ESSENTIAL,
        "net.one97.paytm" to AppCategory.ESSENTIAL,
        "com.phonepe.app" to AppCategory.ESSENTIAL,
    )

    val defaultMonitored: Set<String> = categories.filterValues { it == AppCategory.SOCIAL_MEDIA || it == AppCategory.VIDEO }.keys

    fun category(packageName: String?): AppCategory = packageName?.let { categories[it] } ?: AppCategory.OTHER

    fun isEssential(packageName: String?): Boolean = category(packageName) == AppCategory.ESSENTIAL

    private val packagePattern = Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+$")

    fun isValidPackage(packageName: String?): Boolean = packageName != null && packageName.length <= 255 && packagePattern.matches(packageName)
}
