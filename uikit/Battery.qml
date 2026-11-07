// Battery level for screens shown while nobody is signed in (login, Display, lock).
// A small drawn cell + "87%" in the label style. Hidden when there is no battery.
// Data: set percent/charging/present yourself, or leave `sysfs` on to read
// /sys/class/power_supply/BAT* (needs QML_XHR_ALLOW_FILE_READ=1, as the greeter has).
import QtQuick

Row {
    id: bat
    property real u: 1
    property Tokens tok
    property var textStyle: tok ? tok.label : null
    property color color: tok ? tok.fg : "black"

    property bool sysfs: true
    property int percent: -1
    property bool charging: false
    property bool present: percent >= 0

    readonly property bool low: present && !charging && percent <= 15

    visible: present
    spacing: 6 * u

    Item {
        anchors.verticalCenter: parent.verticalCenter
        width: 17 * bat.u + nub.width; height: 8 * bat.u
        Rectangle {
            id: cell
            width: 17 * bat.u; height: parent.height
            color: "transparent"; border.color: bat.color; border.width: Math.max(1, bat.u)
            radius: 1.5 * bat.u
            Rectangle {
                x: 2 * bat.u; y: 2 * bat.u; height: parent.height - 4 * bat.u
                width: Math.max(bat.u, (parent.width - 4 * bat.u) * Math.min(100, Math.max(0, bat.percent)) / 100)
                radius: 0.5 * bat.u
                color: bat.low ? (bat.tok ? bat.tok.error : "red") : bat.charging && bat.tok ? bat.tok.accent : bat.color
            }
        }
        Rectangle {
            id: nub
            anchors.left: cell.right; anchors.verticalCenter: cell.verticalCenter
            width: 1.5 * bat.u; height: 3.5 * bat.u; color: bat.color
        }
    }
    Label {
        anchors.verticalCenter: parent.verticalCenter
        u: bat.u; tok: bat.tok; textStyle: bat.textStyle; color: bat.color
        text: bat.percent + "%" + (bat.charging ? " · Charging" : "")
    }

    // ---- sysfs reader -------------------------------------------------------
    property int _idx: 0
    function _read(path, done) {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState === XMLHttpRequest.DONE) done(String(x.responseText || "").trim())
        }
        x.open("GET", "file://" + path)
        x.send()
    }
    function refresh() {
        if (!sysfs) return
        var base = "/sys/class/power_supply/BAT" + _idx + "/"
        _read(base + "capacity", function (cap) {
            if (cap === "" || isNaN(Number(cap))) {
                if (_idx < 1) { _idx++; refresh() } else percent = -1
                return
            }
            percent = Number(cap)
            _read(base + "status", function (st) { charging = st === "Charging" })
        })
    }
    Timer { interval: 30000; repeat: true; running: bat.sysfs; triggeredOnStart: true; onTriggered: bat.refresh() }
}
