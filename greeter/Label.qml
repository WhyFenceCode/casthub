// Text set in one of the brand type styles (see brand/tokens.toml [type.*]).
import QtQuick

Text {
    id: label
    property var style
    property real u: 1
    property QtObject tok

    readonly property real px: Math.max(1, style.size * u)

    color: tok ? tok.fg : "black"
    font.family: style.family
    font.pixelSize: px
    font.weight: style.weight
    font.letterSpacing: style.tracking * px
    font.capitalization: style.upper ? Font.AllUppercase : Font.MixedCase
    // Variable-font axes. Axes a font doesn't have are ignored.
    font.variableAxes: ({ "wght": style.weight, "wdth": style.width, "opsz": Math.min(96, Math.max(12, px)) })
    renderType: Text.QtRendering
    textFormat: Text.PlainText
}
