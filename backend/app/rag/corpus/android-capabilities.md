---
title: Android platform capabilities and limits
kind: platform
---
# What MindGuard can do on Android
App usage statistics are available only after the user grants Usage Access (PACKAGE_USAGE_STATS) in system settings. Notifications require POST_NOTIFICATIONS on Android 13+. Full-screen delays and blocks are shown with the Display over other apps permission (SYSTEM_ALERT_WINDOW), which the user grants manually. Focus sessions use foreground timers and optional Do Not Disturb access (ACCESS_NOTIFICATION_POLICY). While the guardian is on, a foreground service with a persistent notification is visible at all times, and background sync runs through the platform JobScheduler. Captions, links and screenshots are analysed only when you share them to MindGuard from the system share sheet.

# What MindGuard does not do
MindGuard does not read private messages or notifications content, does not use accessibility services to inspect other apps, does not capture the screen continuously, does not uninstall or modify other apps, and does not act inside other apps on your behalf. Screenshots are analysed only when you share one manually and have granted screenshot analysis consent.

# When a permission is missing
If Display over other apps is not granted, delays and blocks fall back to a notification that asks for confirmation. If notifications are also disabled, MindGuard cannot show interventions and records the decision only.
