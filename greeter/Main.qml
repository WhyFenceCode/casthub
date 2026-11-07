// Cast Hub login screen: the mode picker (SDDM theme, Qt 6).
// Layout follows design/ pages 3–5, measured on the 1080×675 frame.
// All sizes are frame units × u (u = screen height / 675); horizontal positions use the 12-column grid.
import QtQuick
import "uikit"

Rectangle {
    id: root
    width: 1080
    height: 675

    // ---- scale + grid ------------------------------------------------------
    readonly property real u: height / t.frameHeight
    readonly property real margin: t.gridMargin * u
    readonly property real gutter: t.gridGutter * u
    readonly property real col: (width - 2 * margin - (t.gridColumns - 1) * gutter) / t.gridColumns
    function colX(i) { return margin + i * (col + gutter) }
    readonly property real hair: Math.max(1, Math.round(u))

    // ---- config (theme.conf, theme.conf.user) ------------------------------
    function cfg(key, fallback) {
        var v = (typeof config !== "undefined" && config[key] !== undefined) ? String(config[key]) : ""
        return v.length ? v : fallback
    }
    readonly property string displayUser: cfg("DisplayUser", "casthub")
    readonly property var adminUsers: cfg("AdminUsers", "").split(",").map(function (s) { return s.trim() }).filter(function (s) { return s.length })
    readonly property bool isPrimary: typeof primaryScreen === "undefined" ? true : primaryScreen

    // ---- clock + theme -----------------------------------------------------
    property var fixedNow: null          // preview harness only
    property date now: new Date()
    Timer { interval: 1000; running: root.fixedNow === null; repeat: true; triggeredOnStart: true; onTriggered: root.now = new Date() }
    readonly property date clockNow: fixedNow !== null ? fixedNow : now
    // Light/dark follows the selected user's own Plasma setting. Their session publishes
    // "light" or "dark" to <AppearanceDir>/<user> (modes/workstation/casthub-appearance),
    // because the greeter can't read home folders. No file yet: FallbackScheme (light).
    property var userSchemes: ({})
    function loadScheme(name) {
        if (!name || userSchemes[name] !== undefined) return
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return
            var v = String(x.responseText || "").trim()
            var m = Object.assign({}, root.userSchemes)
            m[name] = (v === "dark" || v === "light") ? v : ""
            root.userSchemes = m
        }
        x.open("GET", "file://" + cfg("AppearanceDir", "/var/lib/casthub/appearance") + "/" + name)
        x.send()
    }
    readonly property bool dark: {
        var s = cfg("ColorScheme", "user")
        if (s === "dark") return true
        if (s === "light") return false
        if (s === "auto") {
            var h = clockNow.getHours()
            return h >= Number(cfg("DarkFrom", "19")) || h < Number(cfg("DarkUntil", "7"))
        }
        var mine = userSchemes[user.name]
        return mine ? mine === "dark" : cfg("FallbackScheme", "light") === "dark"
    }

    Tokens { id: t; dark: root.dark }
    color: t.bg
    Behavior on color { ColorAnimation { duration: t.motionNormal } }

    // ---- modes -------------------------------------------------------------
    readonly property var modes: [
        { key: "workstation", file: "casthub-workstation.desktop", title: "Workstation", sub: "Personal desktop · sign in", password: true, admin: false },
        { key: "display", file: "casthub-display.desktop", title: "Display", sub: "Screen-share receiver · no sign-in", password: false, admin: false },
        { key: "console", file: "casthub-console.desktop", title: "Console", sub: "Maintenance shell · sign in", password: true, admin: true }
    ]
    property int selected: 0
    readonly property var mode: modes[selected]

    // Session file → index in SDDM's sessionModel.
    property var sessionIndex: ({})
    property var sessionFiles: []
    Repeater {
        model: typeof sessionModel !== "undefined" ? sessionModel : 0
        delegate: Item {
            required property int index
            required property string file
            Component.onCompleted: {
                var f = file.split("/").pop()
                var m = Object.assign({}, root.sessionIndex); m[f] = index; root.sessionIndex = m
                var l = root.sessionFiles.slice(); l[index] = f; root.sessionFiles = l
            }
        }
    }
    function modeAvailable(m) { return root.sessionIndex[m.file] !== undefined }

    // ---- users -------------------------------------------------------------
    property var users: []
    property int userIdx: 0
    // One past the list is "Other user": type any account name (hidden accounts, or a
    // machine with a single listed user, where Switch user would otherwise do nothing).
    readonly property bool otherUser: userIdx >= users.length
    readonly property var user: otherUser ? ({ name: otherName.text.trim(), realName: "" }) : users[userIdx]
    function switchUser() {
        userIdx = (userIdx + 1) % (users.length + 1)
        password.text = ""
        message = ""
        if (otherUser) { otherName.text = ""; otherName.forceActiveFocus() } else password.forceActiveFocus()
    }
    Repeater {
        model: typeof userModel !== "undefined" ? userModel : 0
        delegate: Item {
            required property int index
            required property string name
            required property string realName
            Component.onCompleted: {
                if (name === root.displayUser) return
                var l = root.users.slice(); l.push({ name: name, realName: realName || name }); root.users = l
                root.loadScheme(name)
                if (typeof userModel !== "undefined" && name === userModel.lastUser) root.userIdx = l.length - 1
            }
        }
    }
    function isAdmin(name) { return adminUsers.indexOf(name) >= 0 }
    function initials(n) {
        var p = n.split(/\s+/).filter(function (s) { return s.length })
        return ((p[0] || "?")[0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase()
    }

    // ---- state + actions ---------------------------------------------------
    property bool busy: false
    property string message: ""
    Timer { id: messageTimer; interval: 4000; onTriggered: root.message = "" }
    function say(m) { message = m; messageTimer.restart(); shake.restart() }

    function pick(i) {
        selected = (i + modes.length) % modes.length
        if (mode.password) password.forceActiveFocus(); else root.forceActiveFocus()
    }

    function start() {
        if (busy) return
        var m = mode
        if (!modeAvailable(m)) { say("Mode not installed"); return }
        if (m.password && user.name === "") { say("Type a user name"); otherName.forceActiveFocus(); return }
        if (m.admin && !isAdmin(user.name)) { say("Console is for admin accounts only"); return }
        busy = true
        if (m.password) sddm.login(user.name, password.text, sessionIndex[m.file])
        else sddm.login(displayUser, "", sessionIndex[m.file])
    }

    Connections {
        target: typeof sddm !== "undefined" ? sddm : null
        function onLoginFailed() {
            root.busy = false
            password.text = ""
            root.say(root.mode.password ? "Wrong password" : "Display mode could not start")
            if (root.mode.password) password.forceActiveFocus()
        }
        function onLoginSucceeded() { root.busy = false }
    }

    Component.onCompleted: {
        // Preselect the last mode, except after leaving Display: then offer Workstation.
        var last = (typeof sessionModel !== "undefined") ? sessionFiles[sessionModel.lastIndex] : undefined
        var i = 0
        for (var k = 0; k < modes.length; k++) if (modes[k].file === last) i = k
        if (modes[i].key === "display") i = 0
        pick(i)
    }

    // Keyboard-first: ↑/↓ or Tab change mode, Enter starts. Shared by the root and the password field.
    function handleKey(e) {
        if (e.key === Qt.Key_Up || e.key === Qt.Key_Backtab) { pick(selected - 1); e.accepted = true }
        else if (e.key === Qt.Key_Down || e.key === Qt.Key_Tab) { pick(selected + 1); e.accepted = true }
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) { start(); e.accepted = true }
    }
    focus: true
    Keys.onPressed: function (e) { root.handleKey(e) }

    // ---- background grid ---------------------------------------------------
    GridLines { u: root.u; tok: t }

    // ---- top bar -----------------------------------------------------------
    Wordmark {
        x: root.margin
        anchors.verticalCenter: topRule.top
        anchors.verticalCenterOffset: -24 * root.u
        text: root.cfg("Wordmark", "Torsten Young"); u: root.u; tok: t
    }
    Row {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: topRule.top
        anchors.verticalCenterOffset: -24 * root.u
        spacing: 8 * root.u
        Rectangle { width: 6 * root.u; height: width; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
        Label {
            u: root.u; tok: t; textStyle: t.label
            text: (typeof sddm !== "undefined" ? sddm.hostName : "") + " · " + root.cfg("OsLabel", "Fedora Linux")
        }
    }
    Row {
        anchors.right: parent.right
        anchors.rightMargin: root.margin
        anchors.verticalCenter: topRule.top
        anchors.verticalCenterOffset: -24 * root.u
        spacing: 14 * root.u
        Label { u: root.u; tok: t; textStyle: t.label; color: t.muted; text: root.cfg("Location", ""); visible: text.length > 0 }
        Label { u: root.u; tok: t; textStyle: t.label; text: Qt.formatTime(root.clockNow, "HH:mm") }
    }
    Rectangle { id: topRule; x: root.margin; y: 47 * root.u; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }

    // ---- clock + date ------------------------------------------------------
    Label {
        id: bigClock
        x: root.margin - 3 * root.u
        y: 477 * root.u - baselineOffset
        u: root.u; tok: t; textStyle: t.clock
        text: Qt.formatTime(root.clockNow, "HH:mm")
    }
    Label {
        x: root.margin
        y: 541 * root.u - baselineOffset
        u: root.u; tok: t; textStyle: t.date
        text: Qt.formatDate(root.clockNow, "dddd d MMMM")
    }

    // ---- mode panel (primary screen only) ----------------------------------
    Item {
        id: panel
        visible: root.isPrimary
        x: root.colX(8)
        width: root.width - root.margin - x
        height: root.height

        Row {
            y: 201 * root.u - height / 2
            spacing: 6 * root.u
            Rectangle { width: 6 * root.u; height: width; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
            Label { u: root.u; tok: t; textStyle: t.label; text: "Mode" }
        }

        Column {
            id: rows
            y: 216 * root.u
            width: parent.width
            Rectangle { width: parent.width; height: root.hair; color: t.line }
            Repeater {
                model: root.modes
                Column {
                    required property int index
                    required property var modelData
                    width: rows.width
                    ModeRow {
                        width: parent.width
                        u: root.u; tok: t
                        number: index + 1
                        title: modelData.title
                        subtitle: (modelData.admin && !root.isAdmin(root.user.name)) ? "Maintenance shell · admin accounts only" : modelData.sub
                        available: root.modeAvailable(modelData) && !(modelData.admin && !root.isAdmin(root.user.name))
                        selected: root.selected === index
                        onPicked: root.pick(index)
                    }
                    Rectangle { width: parent.width; height: root.hair; color: t.line }
                }
            }
        }

        // Sign-in block (Workstation, Console)
        Item {
            id: signIn
            visible: root.mode.password
            width: parent.width
            y: 388 * root.u
            height: 105 * root.u

            Rectangle {
                id: avatar
                width: 36 * root.u; height: width; radius: width / 2
                y: 1 * root.u
                color: t.well
                Label { anchors.centerIn: parent; u: root.u; tok: t; textStyle: t.label; text: root.otherUser ? "?" : root.initials(root.user.realName) }
            }
            Column {
                anchors.left: avatar.right
                anchors.leftMargin: 11 * root.u
                anchors.verticalCenter: avatar.verticalCenter
                spacing: 3 * root.u
                Label { visible: !root.otherUser; u: root.u; tok: t; textStyle: t.name; text: root.user.realName }
                Label {
                    visible: !root.otherUser
                    u: root.u; tok: t; textStyle: t.label; color: t.muted
                    font.capitalization: Font.MixedCase
                    text: root.user.name + "@" + (typeof sddm !== "undefined" ? sddm.hostName : "")
                }
                // Other user: the name line becomes a field.
                Item {
                    visible: root.otherUser
                    width: signIn.width - avatar.width - 11 * root.u; height: nameProbe.height
                    Label { id: nameProbe; visible: otherName.text === ""; u: root.u; tok: t; textStyle: t.name; color: t.muted; text: "User name" }
                    TextInput {
                        id: otherName
                        width: parent.width
                        color: t.fg
                        font: nameProbe.font
                        enabled: !root.busy
                        Keys.priority: Keys.BeforeItem
                        Keys.onPressed: function (e) {
                            if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Tab) { password.forceActiveFocus(); e.accepted = true }
                        }
                        onTextChanged: root.message = ""
                        cursorDelegate: Rectangle { width: Math.max(1, root.u); color: t.accent; visible: otherName.activeFocus }
                    }
                }
                Label {
                    visible: root.otherUser
                    u: root.u; tok: t; textStyle: t.label; color: t.muted
                    font.capitalization: Font.MixedCase
                    text: "Other user@" + (typeof sddm !== "undefined" ? sddm.hostName : "")
                }
            }
            Label { y: 57 * root.u - height / 2; u: root.u; tok: t; textStyle: t.label; color: t.muted; text: "Password" }
            // Errors sit next to the field they're about.
            Chip {
                anchors.right: parent.right
                y: 57 * root.u - height / 2
                u: root.u; tok: t; kind: "error"
                text: root.message
                visible: root.message.length > 0
            }
            TextInput {
                id: password
                y: 85 * root.u - height / 2
                width: parent.width
                echoMode: TextInput.Password
                passwordCharacter: "■"
                color: t.muted
                font.family: t.fontMono
                font.pixelSize: 6 * root.u
                font.letterSpacing: 7 * root.u
                selectByMouse: false
                enabled: !root.busy
                Keys.priority: Keys.BeforeItem
                Keys.onPressed: function (e) { root.handleKey(e) }
                onTextChanged: if (text.length) root.message = ""
                cursorDelegate: Rectangle { width: Math.max(1, root.u); color: t.accent; visible: password.activeFocus }
            }
            Rectangle { y: 104 * root.u; width: parent.width; height: root.hair; color: t.line }

            SequentialAnimation {
                id: shake
                NumberAnimation { target: signIn; property: "x"; to: -8 * root.u; duration: 50 }
                NumberAnimation { target: signIn; property: "x"; to: 7 * root.u; duration: 70 }
                NumberAnimation { target: signIn; property: "x"; to: -4 * root.u; duration: 60 }
                NumberAnimation { target: signIn; property: "x"; to: 0; duration: 60 }
            }
        }

        // Display explainer
        Item {
            visible: !root.mode.password
            width: parent.width
            y: 388 * root.u
            height: 105 * root.u
            Label {
                y: 12 * root.u
                width: parent.width - 30 * root.u
                wrapMode: Text.WordWrap
                lineHeight: 1.22
                u: root.u; tok: t; textStyle: t.body
                text: "No sign-in needed. This screen turns into a receiver for AirPlay and Miracast until you leave display mode."
            }
            Label {
                y: 84 * root.u - height / 2
                visible: root.message.length === 0
                u: root.u; tok: t; textStyle: t.label; color: t.muted
                text: "Files and accounts stay locked"
            }
            Chip {
                y: 84 * root.u - height / 2
                visible: root.message.length > 0
                u: root.u; tok: t; kind: "error"
                text: root.message
            }
            Rectangle { y: 104 * root.u; width: parent.width; height: root.hair; color: t.line }
        }

        Row {
            anchors.right: parent.right
            y: 510 * root.u
            spacing: 8 * root.u
            PillButton {
                u: root.u; tok: t; kind: "outline"; text: "Switch user"
                onClicked: {
                    if (!root.mode.password) { root.pick(0); return }   // from Display: go to sign-in
                    root.switchUser()
                }
            }
            PillButton {
                u: root.u; tok: t; kind: "solid"
                text: root.busy ? "Starting…" : (root.mode.password ? "Sign in" : "Start display")
                onClicked: root.start()
            }
        }
    }

    // ---- footer ------------------------------------------------------------
    Rectangle { id: bottomRule; x: root.margin; y: root.height - 71 * root.u; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }
    readonly property real footerMid: bottomRule.y + 36 * root.u   // centre line of the Restart / Shut down pills
    Label {
        x: root.margin
        y: root.footerMid - height / 2
        u: root.u; tok: t; textStyle: t.label; color: t.muted
        text: root.cfg("OsLabel", "Fedora Linux") + " · " + root.cfg("SessionLabel", "Wayland")
    }
    Label {
        visible: root.isPrimary
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.footerMid - height / 2
        u: root.u; tok: t; textStyle: t.label; color: t.muted
        text: root.mode.password ? "Caps lock · " + ((typeof keyboard !== "undefined" && keyboard.capsLock) ? "On" : "Off")
             : "Tab · change mode · ↵ start"
    }
    Row {
        visible: root.isPrimary
        anchors.right: parent.right
        anchors.rightMargin: root.margin
        y: bottomRule.y + 19 * root.u
        spacing: 6 * root.u
        PillButton {
            u: root.u; tok: t; kind: "faint"; text: "Restart"; height: 34 * root.u
            visible: typeof sddm === "undefined" || sddm.canReboot
            onClicked: sddm.reboot()
        }
        PillButton {
            u: root.u; tok: t; kind: "faint"; text: "Shut down"; height: 34 * root.u
            visible: typeof sddm === "undefined" || sddm.canPowerOff
            onClicked: sddm.powerOff()
        }
    }
}
