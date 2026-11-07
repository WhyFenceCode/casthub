// Text set in one of the brand type styles (see brand/tokens.toml [type.*]).
import QtQuick

Text {
    id: label
    property var textStyle
    property real u: 1
    property Tokens tok

    readonly property real px: Math.max(1, textStyle.size * u)

    color: tok ? tok.fg : "black"
    font.family: textStyle.family
    font.pixelSize: px
    font.weight: textStyle.weight
    font.letterSpacing: textStyle.tracking * px
    font.capitalization: textStyle.upper ? Font.AllUppercase : Font.MixedCase
    // Variable-font axes. Axes a font doesn't have are ignored.
    font.variableAxes: ({ "wght": textStyle.weight, "wdth": textStyle.width, "opsz": Math.min(96, Math.max(12, px)) })
    renderType: Text.QtRendering
    textFormat: Text.PlainText
}
