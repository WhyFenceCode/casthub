// Status chip: pastel fill, ink text. Status colours are only ever used this way
// (pastel text on the light theme is unreadable; see brand/tokens.toml).
import QtQuick

Rectangle {
    id: chip
    property string text
    property string kind: "error"        // error | warning | success | accent
    property real u: 1
    property Tokens tok

    implicitWidth: caption.implicitWidth + 14 * u
    implicitHeight: 17 * u
    radius: height / 2
    color: kind === "success" ? tok.success : kind === "warning" ? tok.warning : kind === "accent" ? tok.accent : tok.error

    Label {
        id: caption
        anchors.centerIn: parent
        text: chip.text
        u: chip.u
        tok: chip.tok
        textStyle: chip.tok.label
        color: chip.tok.onStatus
    }
}
