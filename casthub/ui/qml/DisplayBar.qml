// Display-mode top bar (design pages 12-13): wordmark, "■ DISPLAY MODE", receiver name, rule.
import QtQuick
import "uikit"

Item {
    id: bar
    property real u: 1
    property real ux: 1
    property real uy: 1
    property Tokens tok
    property real margin: 30
    property real modeX: 376
    property string modeText: "Display mode"
    property string receiverName: ""
    anchors.left: parent.left; anchors.right: parent.right
    height: 54 * uy

    Wordmark { x: bar.margin; y: 27.5 * bar.uy - height / 2; u: bar.u * 1.32; tok: bar.tok }   // larger here than on 1080x675 pages
    Row {
        x: bar.modeX; y: 27 * bar.uy - height / 2
        spacing: 11 * bar.ux
        Rectangle { width: 8 * bar.u; height: width; anchors.verticalCenter: parent.verticalCenter; color: bar.tok.accent }
        Label { u: bar.u; tok: bar.tok; textStyle: bar.tok.dlabel; text: bar.modeText }
    }
    Label {
        anchors.right: parent.right; anchors.rightMargin: bar.margin
        y: 27 * bar.uy - height / 2
        u: bar.u; tok: bar.tok; textStyle: bar.tok.dlabel; color: bar.tok.muted
        text: bar.receiverName
    }
    Rectangle {
        x: bar.margin; y: 54 * bar.uy
        width: bar.width - 2 * bar.margin; height: Math.max(1, Math.round(bar.u))
        color: bar.tok.fg
    }
}
