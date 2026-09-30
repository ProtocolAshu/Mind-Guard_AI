"""App catalog: package → category, plus natural-language app aliases.

Used by the Goal Agent (NL → package names), the Content Agent (metadata-only
classification, which needs no content access at all) and the guardrails
(essential apps can never be restricted).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.common import AppCategory, ContentCategory


@dataclass(frozen=True)
class AppInfo:
    package: str
    name: str
    category: AppCategory
    default_content: ContentCategory
    aliases: tuple[str, ...] = ()

    @property
    def is_social(self) -> bool:
        return self.category in (AppCategory.SOCIAL_MEDIA, AppCategory.VIDEO)


APPS: tuple[AppInfo, ...] = (
    AppInfo("com.instagram.android", "Instagram", AppCategory.SOCIAL_MEDIA, ContentCategory.SOCIAL, ("instagram", "insta", "ig")),
    AppInfo("com.instagram.barcelona", "Threads", AppCategory.SOCIAL_MEDIA, ContentCategory.SOCIAL, ("threads",)),
    AppInfo("com.facebook.katana", "Facebook", AppCategory.SOCIAL_MEDIA, ContentCategory.SOCIAL, ("facebook", "fb")),
    AppInfo("com.twitter.android", "X", AppCategory.SOCIAL_MEDIA, ContentCategory.NEWS, ("twitter", "x app", "tweets")),
    AppInfo("com.snapchat.android", "Snapchat", AppCategory.SOCIAL_MEDIA, ContentCategory.SOCIAL, ("snapchat", "snap")),
    AppInfo("com.reddit.frontpage", "Reddit", AppCategory.SOCIAL_MEDIA, ContentCategory.SOCIAL, ("reddit",)),
    AppInfo("com.pinterest", "Pinterest", AppCategory.SOCIAL_MEDIA, ContentCategory.SOCIAL, ("pinterest",)),
    AppInfo("com.linkedin.android", "LinkedIn", AppCategory.SOCIAL_MEDIA, ContentCategory.CAREER, ("linkedin",)),
    AppInfo("com.zhiliaoapp.musically", "TikTok", AppCategory.VIDEO, ContentCategory.SHORT_FORM_VIDEO, ("tiktok", "tik tok")),
    AppInfo("com.google.android.youtube", "YouTube", AppCategory.VIDEO, ContentCategory.UNKNOWN, ("youtube", "yt")),
    AppInfo("com.netflix.mediaclient", "Netflix", AppCategory.VIDEO, ContentCategory.ENTERTAINMENT, ("netflix",)),
    AppInfo("in.startv.hotstar", "JioHotstar", AppCategory.VIDEO, ContentCategory.ENTERTAINMENT, ("hotstar", "jiohotstar")),
    AppInfo("com.amazon.avod.thirdpartyclient", "Prime Video", AppCategory.VIDEO, ContentCategory.ENTERTAINMENT, ("prime video",)),
    AppInfo("com.discord", "Discord", AppCategory.MESSAGING, ContentCategory.COMMUNICATION, ("discord",)),
    AppInfo("com.whatsapp", "WhatsApp", AppCategory.MESSAGING, ContentCategory.COMMUNICATION, ("whatsapp",)),
    AppInfo("org.telegram.messenger", "Telegram", AppCategory.MESSAGING, ContentCategory.COMMUNICATION, ("telegram",)),
    AppInfo("com.android.chrome", "Chrome", AppCategory.BROWSER, ContentCategory.UNKNOWN, ("chrome", "browser")),
    AppInfo("com.supercell.clashofclans", "Clash of Clans", AppCategory.GAMES, ContentCategory.GAMING, ("clash of clans",)),
    AppInfo("com.dts.freefireth", "Free Fire", AppCategory.GAMES, ContentCategory.GAMING, ("free fire",)),
    AppInfo("com.pubg.imobile", "BGMI", AppCategory.GAMES, ContentCategory.GAMING, ("bgmi", "pubg")),
    AppInfo("org.coursera.android", "Coursera", AppCategory.EDUCATION, ContentCategory.EDUCATION, ("coursera",)),
    AppInfo("com.duolingo", "Duolingo", AppCategory.EDUCATION, ContentCategory.EDUCATION, ("duolingo",)),
    AppInfo("org.khanacademy.android", "Khan Academy", AppCategory.EDUCATION, ContentCategory.EDUCATION, ("khan academy",)),
    AppInfo("com.google.android.apps.docs", "Google Drive", AppCategory.PRODUCTIVITY, ContentCategory.PRODUCTIVITY, ("drive",)),
    AppInfo("com.notion.id", "Notion", AppCategory.PRODUCTIVITY, ContentCategory.PRODUCTIVITY, ("notion",)),
    AppInfo("com.google.android.gm", "Gmail", AppCategory.PRODUCTIVITY, ContentCategory.COMMUNICATION, ("gmail", "email")),
    AppInfo("com.google.android.apps.magazines", "Google News", AppCategory.NEWS, ContentCategory.NEWS, ("google news",)),
    AppInfo("com.google.android.dialer", "Phone", AppCategory.ESSENTIAL, ContentCategory.COMMUNICATION, ("phone", "dialer", "calls")),
    AppInfo("com.google.android.apps.messaging", "Messages", AppCategory.ESSENTIAL, ContentCategory.COMMUNICATION, ("sms", "messages")),
    AppInfo("com.google.android.apps.maps", "Maps", AppCategory.ESSENTIAL, ContentCategory.PRODUCTIVITY, ("maps", "google maps")),
    AppInfo("com.google.android.deskclock", "Clock", AppCategory.ESSENTIAL, ContentCategory.PRODUCTIVITY, ("clock", "alarm")),
    AppInfo("com.google.android.contacts", "Contacts", AppCategory.ESSENTIAL, ContentCategory.COMMUNICATION, ("contacts",)),
    AppInfo("com.phonepe.app", "PhonePe", AppCategory.ESSENTIAL, ContentCategory.PRODUCTIVITY, ("phonepe", "upi")),
    AppInfo("com.google.android.apps.nbu.paisa.user", "Google Pay", AppCategory.ESSENTIAL, ContentCategory.PRODUCTIVITY, ("gpay", "google pay")),
)

_BY_PACKAGE = {a.package: a for a in APPS}

# Framework packages that must never be restricted even if unknown to the catalog.
ESSENTIAL_PACKAGE_PREFIXES = (
    "com.android.phone", "com.android.server.telecom", "com.android.emergency", "com.google.android.dialer",
    "com.android.settings", "com.android.systemui", "com.mindguard",
)


def lookup(package: str | None) -> AppInfo | None:
    return _BY_PACKAGE.get(package or "")


def category_of(package: str | None) -> AppCategory:
    info = lookup(package)
    if info:
        return info.category
    if package and package.startswith(ESSENTIAL_PACKAGE_PREFIXES):
        return AppCategory.ESSENTIAL
    return AppCategory.OTHER


def is_essential(package: str | None) -> bool:
    return category_of(package) is AppCategory.ESSENTIAL


def find_apps_in_text(text: str) -> list[AppInfo]:
    import re

    lowered = f" {text.lower()} "
    found: list[AppInfo] = []
    for app in APPS:
        for alias in app.aliases:
            if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered):
                found.append(app)
                break
    return found


SOCIAL_LIKE_CATEGORIES = frozenset({AppCategory.SOCIAL_MEDIA, AppCategory.VIDEO})
