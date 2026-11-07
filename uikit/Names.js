// Window names in the Cast Hub style (design pages 7-11): "FILES · ~/PROJECTS".
// Shared by the top bar and the window decoration.
.pragma library

var APPS = {
    "org.kde.konsole": "Console",
    "org.kde.dolphin": "Files",
    "org.kde.systemsettings": "Settings",
    "org.kde.gwenview": "Image",
    "org.kde.okular": "Document",
    "org.kde.kate": "Editor",
    "org.kde.kwrite": "Editor",
    "org.mozilla.firefox": "Web",
    "firefox": "Web"
};

var BY_NAME = { "Konsole": "Console", "Dolphin": "Files", "System Settings": "Settings", "Gwenview": "Image",
                "Okular": "Document", "Kate": "Editor", "KWrite": "Editor", "Mozilla Firefox": "Web", "Firefox": "Web" };

// From a bare window caption ("Projects — Dolphin"), as window decorations only get that.
function captionFromTitle(title, sep) {
    var t = title || "", at = t.lastIndexOf(" — ");
    if (at > 0) {
        var app = t.slice(at + 3), rest = t.slice(0, at);
        return (BY_NAME[app] || app) + (sep || " · ") + rest;
    }
    return t;
}

// Short app label: our names for the common apps, else the app's own name.
function appLabel(appId, appName) {
    var id = (appId || "").replace(/\.desktop$/, "");
    if (APPS[id]) return APPS[id];
    if (BY_NAME[appName]) return BY_NAME[appName];
    return appName || id.split(".").pop() || "";
}

// "Projects — Dolphin" → "Projects"; "~ : bash — Konsole" → "~ : bash".
function cleanTitle(title, appName) {
    var t = title || "";
    var seps = [" — ", " – ", " - "];
    for (var i = 0; i < seps.length; i++) {
        var at = t.lastIndexOf(seps[i]);
        if (at > 0 && (!appName || t.slice(at + seps[i].length).indexOf(appName) >= 0 || i === 0)) {
            t = t.slice(0, at);
            break;
        }
    }
    return t;
}

// "FILES · ~/PROJECTS" (uppercase is applied by the label style).
function caption(appId, appName, title, sep) {
    var a = appLabel(appId, appName), c = cleanTitle(title, appName);
    if (!c || c === a) return a;
    return a + (sep || " · ") + c;
}
