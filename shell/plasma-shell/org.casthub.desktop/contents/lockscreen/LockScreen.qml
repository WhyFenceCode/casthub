// Cast Hub lock screen, also what you see when waking from sleep (SPEC D22).
// Left: time and date. Right: the signature on two lines. Below the rule: the password,
// in the login screen's style. Unlocking uses kscreenlocker's `authenticator` exactly as
// Plasma's own lock screen does (respond / succeeded / failed / startAuthenticating).
// If this file ever fails to load, kscreenlocker shows its fallback; recovery is in
// docs/RECOVERY.md (Ctrl+Alt+F3, `loginctl unlock-sessions`).
import QtQuick
import QtQuick.Window
import org.kde.coreaddons as KCoreAddons
import org.kde.plasma.private.sessions
import org.kde.plasma.private.keyboardindicator as KeyboardIndicator
import org.kde.plasma.private.battery as PlasmaBattery
import "uikit"

Rectangle {
    id: root

    // ---- what kscreenlocker_greet looks for -------------------------------------
    property bool viewVisible: false
    property bool debug: false
    property string notification
    signal clearPassword()
    signal notificationRepeated()
    onClearPassword: password.text = ""

    // ---- frame (1080 x 675 units, as the login screen) ---------------------------
    readonly property real u: Math.min(width / 1080, height / t.frameHeight)
    readonly property real margin: t.gridMargin * u
    readonly property real gutter: t.gridGutter * u
    readonly property real col: (width - 2 * margin - (t.gridColumns - 1) * gutter) / t.gridColumns
    function colX(i) { return margin + i * (col + gutter) }
    readonly property real hair: Math.max(1, Math.round(u))
    Tokens { id: t; dark: true }
    color: t.bootBg
    implicitWidth: 1080; implicitHeight: 675

    // ---- clock --------------------------------------------------------------------
    property date now: new Date()
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = new Date() }
    function pad(n) { return n < 10 ? "0" + n : "" + n }
    readonly property var days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    readonly property var months: ["January", "February", "March", "April", "May", "June", "July",
                                   "August", "September", "October", "November", "December"]

    KCoreAddons.KUser { id: kuser }
    SessionManagement { id: sessions }
    Connections { target: sessions; function onAboutToSuspend() { root.clearPassword() } }
    KeyboardIndicator.KeyState { id: caps; key: Qt.Key_CapsLock }

    // ---- unlocking ------------------------------------------------------------------
    property string message: ""
    Timer { id: messageTimer; interval: 4000; onTriggered: root.message = "" }
    function say(m) { if (m) { message = m; messageTimer.restart() } }
    function submit() {
        if (password.text.length === 0) return
        // After a few wrong tries PAM makes you wait; this Plasma has no change signal for that,
        // so say so and let the next Enter try again (no queued password left behind).
        if (authenticator.pamTimeout) { say("Wait a moment, then press Enter again"); return }
        authenticator.respond(password.text)
    }
    Connections {
        target: authenticator
        function onFailed(kind) {
            if (kind != 0) return              // fingerprint/smartcard attempts report themselves
            root.say("Wrong password")
            password.text = ""
            authenticator.startAuthenticating()
            shake.restart()
        }
        function onSucceeded() {
            authenticator.stopAuthenticating()
            Qt.quit()
        }
        function onInfoMessageChanged() { root.say(authenticator.infoMessage) }
        function onErrorMessageChanged() { root.say(authenticator.errorMessage) }
    }
    Component.onCompleted: { authenticator.startAuthenticating(); password.forceActiveFocus() }
    Timer {   // keeps the PAM conversation alive while locked (as Plasma's lock screen does)
        interval: 1000; repeat: true
        running: !authenticator.unlocked
        onTriggered: authenticator.startAuthenticating()
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onPressed: password.forceActiveFocus()
        onPositionChanged: if (root.Window.window) root.Window.window.requestActivate()
    }

    GridLines { u: root.u; tok: t; margin: root.margin; gutter: root.gutter }

    // ---- top bar --------------------------------------------------------------------
    Wordmark { x: root.margin; y: 23.5 * root.u - height / 2; u: root.u; tok: t }
    Row {
        x: root.colX(4); y: 23.5 * root.u - height / 2
        spacing: 8 * root.u
        Rectangle { width: 6 * root.u; height: width; color: t.accent; anchors.verticalCenter: parent.verticalCenter }
        Label { u: root.u; tok: t; textStyle: t.label; text: "Locked" }
    }
    PlasmaBattery.BatteryControlModel { id: batteryControl }
    Row {
        anchors.right: parent.right; anchors.rightMargin: root.margin
        y: 23.5 * root.u - height / 2
        spacing: 14 * root.u
        Battery {
            anchors.verticalCenter: parent.verticalCenter
            u: root.u; tok: t; sysfs: false
            present: batteryControl.hasInternalBatteries
            percent: batteryControl.percent
            charging: batteryControl.pluggedIn && batteryControl.percent < 100
        }
        Label { u: root.u; tok: t; textStyle: t.label; color: t.muted; text: kuser.loginName }
    }
    Rectangle { x: root.margin; y: 47 * root.u; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }

    // ---- left: time and date ------------------------------------------------------------
    Label {
        x: root.margin - 6 * root.u
        y: 330 * root.u - baselineOffset
        u: root.u; tok: t; textStyle: t.dclock
        text: root.pad(root.now.getHours()) + ":" + root.pad(root.now.getMinutes())
    }
    Label {
        x: root.margin
        y: 405 * root.u - baselineOffset
        u: root.u; tok: t; textStyle: t.ddate
        text: root.days[root.now.getDay()] + " " + root.now.getDate() + " " + root.months[root.now.getMonth()]
    }

    // ---- right: the signature on two lines -----------------------------------------------
    Image {
        id: sig
        source: "signature.png"
        fillMode: Image.PreserveAspectFit
        smooth: true; mipmap: true
        x: root.width - root.margin - width
        width: Math.min(root.width - root.margin - root.colX(7), 340 * root.u * sourceSize.width / Math.max(1, sourceSize.height))
        height: width * sourceSize.height / Math.max(1, sourceSize.width)
        y: 425 * root.u - height
    }

    Rectangle { x: root.margin; y: 440 * root.u; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }

    // ---- below the rule: who, and the password -------------------------------------------
    Column {
        x: root.margin; y: 462 * root.u
        spacing: 6 * root.u
        Label { u: root.u; tok: t; textStyle: t.name; text: kuser.fullName || kuser.loginName }
        Label { u: root.u; tok: t; textStyle: t.label; color: t.muted; text: "Workstation · locked" }
    }

    Item {
        id: signIn
        x: root.colX(7); y: 462 * root.u
        width: root.width - root.margin - x
        height: 70 * root.u
        Label { y: 6 * root.u; u: root.u; tok: t; textStyle: t.label; color: t.muted; text: "Password" }
        Chip {
            anchors.right: parent.right
            y: 6 * root.u + 6 * root.u - height / 2
            visible: chipText !== ""
            readonly property string chipText: root.message !== "" ? root.message : (caps.locked ? "Caps Lock is on" : "")
            u: root.u; tok: t; kind: root.message !== "" ? "error" : "warning"
            text: chipText
        }
        TextInput {
            id: password
            y: 38 * root.u - height / 2
            width: parent.width
            echoMode: TextInput.Password
            passwordCharacter: "■"
            color: t.muted
            font.family: t.fontMono
            font.pixelSize: 6 * root.u
            font.letterSpacing: 7 * root.u
            focus: true
            onAccepted: root.submit()
            Keys.onEscapePressed: text = ""
            onTextChanged: if (text.length) root.message = ""
            cursorDelegate: Rectangle { width: Math.max(1, root.u); color: t.accent; visible: password.activeFocus }
        }
        Label {
            visible: password.text.length === 0
            y: 38 * root.u - height / 2
            u: root.u; tok: t; textStyle: t.label; color: t.muted
            text: "Type to unlock"
        }
        Rectangle { y: 57 * root.u; width: parent.width; height: root.hair; color: t.line }
        SequentialAnimation {
            id: shake
            NumberAnimation { target: signIn; property: "x"; to: root.colX(7) - 8 * root.u; duration: 50 }
            NumberAnimation { target: signIn; property: "x"; to: root.colX(7) + 7 * root.u; duration: 70 }
            NumberAnimation { target: signIn; property: "x"; to: root.colX(7) - 4 * root.u; duration: 60 }
            NumberAnimation { target: signIn; property: "x"; to: root.colX(7); duration: 60 }
        }
    }

    // ---- footer --------------------------------------------------------------------------
    Rectangle { id: bottomRule; x: root.margin; y: root.height - 71 * root.u; width: root.width - 2 * root.margin; height: root.hair; color: t.fg }
    Label {
        x: root.margin
        y: bottomRule.y + 36 * root.u - height / 2
        u: root.u; tok: t; textStyle: t.label; color: t.muted
        text: "Enter · unlock   Esc · clear"
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: root.margin
        y: bottomRule.y + 36 * root.u - height / 2
        spacing: 8 * root.u
        PillButton { u: root.u * 0.8; tok: t; kind: "faint"; text: "Switch user"; visible: sessions.canSwitchUser; onClicked: sessions.switchUser() }
        PillButton { u: root.u * 0.8; tok: t; kind: "faint"; text: "Sleep"; visible: sessions.canSuspend; onClicked: sessions.suspend() }
        PillButton { u: root.u * 0.8; tok: t; kind: "solid"; text: "Unlock"; onClicked: root.submit() }
    }
}
