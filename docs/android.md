# Android application

Kotlin, 10 Gradle modules, `minSdk 29`, `targetSdk 34`. The only third-party dependency is kotlinx-coroutines:
no AndroidX, no networking library, no DI framework. That keeps the app small, auditable and fully type-checkable
offline, at the cost of writing some plumbing by hand.

| Module | Contents |
|---|---|
| `domain` | Pure Kotlin: models, app catalog, `SessionTracker`, `CommandValidator`, `OfflinePolicy`, backoff, JSON writer. Unit-tested on the JVM |
| `core` | Dispatchers, clock, log redaction, `SecureStore` (Keystore AES-GCM), `JsonHttp` (HttpURLConnection, HTTPS-only) |
| `data` | `MindGuardApi` (typed, single-flight refresh), `TokenStore`, SQLite outbox `EventQueue` + `EventUploader`, `PolicyCache` |
| `permissions` | `GuardianPermission` catalogue, runtime and special-access checks, settings intents |
| `monitoring` | `UsageEventSource` (UsageStatsManager), `GuardianService` (foreground), `SyncJobService` (JobScheduler), `BootReceiver` |
| `intervention` | `InterventionController` with notification and overlay implementations, capability-aware composite, action receiver |
| `analytics` | Today summary parsing and formatting |
| `ai` | Share-sheet content analysis (text and screenshots) |
| `settings` | Local preferences (guardian state, monitored apps, focus end, API base URL) |
| `app` | Application/DI container, `MainActivity` (sign-in, 8-step onboarding, today), `ShareActivity`, programmatic UI kit |

## Permissions

| Permission | Type | Why | Without it |
|---|---|---|---|
| `INTERNET`, `ACCESS_NETWORK_STATE` | normal | Talk to the API; JobScheduler network constraint | The app cannot sync |
| `PACKAGE_USAGE_STATS` | special access | See which chosen app is foreground and for how long | No sessions are noticed, so nothing ever intervenes |
| `POST_NOTIFICATIONS` | runtime (13+) | Interventions and the persistent protection notice | Interventions cannot be shown; decisions are only recorded |
| `SYSTEM_ALERT_WINDOW` | special access | Pause screens and delay gates over a limited app | Restrictions soften to notifications |
| `ACCESS_NOTIFICATION_POLICY` | special access | Silence notifications during focus sessions | Focus sessions run, notifications keep arriving |
| `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_SPECIAL_USE` | normal | Keep monitoring visibly while the guardian is on | Monitoring cannot run reliably |
| `RECEIVE_BOOT_COMPLETED` | normal | Resume protection after reboot **if** the user enabled it | Protection must be restarted manually |

No location, contacts, camera, microphone, accessibility service or `QUERY_ALL_PACKAGES`. Package visibility is a
`queries` list of the monitorable apps. Backup and device transfer are excluded; cleartext traffic is disabled except
`10.0.2.2` in debug.

## Behaviour

- **Monitoring**: the guardian polls `UsageStatsManager` every 20 s, converts foreground changes into sessions with
  deterministic event ids, and enqueues `APP_OPENED` / `SESSION_EXTENDED` (5-minute checkpoints) / `APP_CLOSED`.
- **Evaluation**: at most once every two minutes per live session, and never before one minute of use. Queued events
  upload first so the server reasons on current context.
- **Execution**: the server's command is validated again on device (expiry, duration caps, essential apps,
  reversibility, package shape) before a notification or overlay is shown. Restrictions always offer "Allow once"
  and "Emergency", and going home is always possible.
- **Offline**: the outbox retries with exponential backoff and full jitter; cached rules can only produce a gentle
  reminder — the device never blocks on its own authority.
- **Multimodal input** is user-initiated only: captions, links or screenshots shared to MindGuard from the system
  share sheet.

## Build

```bash
cd android
gradle wrapper --gradle-version 8.10.2   # the wrapper jar is not committed
./gradlew :domain:test                   # JVM unit tests
./gradlew :app:assembleDebug             # APK
```

### Offline verification (used here)

Google's Maven repository and Maven Central were unreachable in the build environment, so **no APK was produced and
Gradle never ran**. Instead every production source was type-checked against the real Android 34 SDK jar and the real
kotlinx-coroutines 1.10.2 artifact, with warnings treated as errors, and the domain tests were compiled and executed
on the JVM:

```bash
KOTLINC=…/kotlinc ANDROID_JAR=…/android-34.jar COROUTINES_JAR=…/kotlinx-coroutines-core-jvm.jar \
  android/tools/offline-check/verify.sh
# 21 sources → 137 classes, 0 warnings; 13 domain tests passed
```

`android/tools/offline-check/junit-shim/` contains a minimal `org.junit` annotation and assertion stand-in used only
by that script, because JUnit itself could not be downloaded. The Gradle build uses the real `junit:junit:4.13.2`.

## Not verified here

APK assembly, installation on a device or emulator, instrumented tests, real `UsageStatsManager` output, overlay
rendering, Keystore behaviour on a real device, battery and network impact, and OEM background-process restrictions
(which are the usual reason such apps stop working in the field).
