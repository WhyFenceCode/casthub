// Cast Hub window frame (design pages 10-11). Light/dark follows the colour scheme: the
// title-bar colour Plasma passes in is our surface token, so its lightness picks the variant.
// KWin cannot clip a QML decoration's client corners, so the frame keeps a 4 px edge in the
// window colour; the client's square corners sit inside the rounded outline.
import QtQuick
import QtQuick.Window
import org.kde.kwin.decoration
import "uikit"
import "uikit/Names.js" as Names

Decoration {
    id: root
    readonly property real u: Math.max(1, (Screen.height > 0 ? Screen.height : 1080) / t.frameHeight)
    readonly property bool dark: options.titleBarColor.hslLightness < 0.5
    readonly property bool maxed: decoration.client.maximized
    readonly property bool active: decoration.client.active
    readonly property int edge: maxed ? 0 : Math.round(4 * u / 1.6)
    readonly property int titleH: Math.round(31 * u)
    readonly property int radius: maxed ? 0 : Math.round(10 * u / 1.6)
    readonly property int shadow: maxed ? 0 : Math.round(18 * u / 1.6)
    alpha: true

    Tokens { id: t; dark: root.dark }
    DecorationOptions { id: options; deco: decoration }

    function applyBorders() {
        borders.setBorders(edge)
        borders.setTitle(titleH)
        maximizedBorders.setBorders(0)
        maximizedBorders.setTitle(titleH)
        extendedBorders.setAllBorders(Math.round(6 * u / 1.6))   // easier resize grab
        padding.setAllBorders(shadow)
    }
    onMaxedChanged: applyBorders()
    Component.onCompleted: applyBorders()

    // Soft shadow: a few expanding, fading outlines (cheap; no blur pass per window).
    Repeater {
        model: root.maxed ? 0 : 6
        Rectangle {
            required property int index
            readonly property real grow: (index + 1) * root.shadow / 6
            x: root.shadow - grow; y: root.shadow - grow + grow * 0.35
            width: parent.width - 2 * x; height: parent.height - 2 * (root.shadow - grow)
            radius: root.radius + grow
            color: "transparent"
            border.width: root.shadow / 6 + 1
            border.color: Qt.rgba(0, 0, 0, (root.active ? 0.07 : 0.04) * (1 - index / 6))
        }
    }

    // Frame: window colour with a 1 px outline.
    Rectangle {
        id: frame
        x: root.maxed ? 0 : root.shadow; y: x
        width: parent.width - 2 * x; height: parent.height - 2 * y
        radius: root.radius
        color: t.surface
        border.width: root.maxed ? 0 : 1
        border.color: root.active ? t.fg : t.line

        Item {
            id: titleBar
            x: 1; y: 1
            width: parent.width - 2; height: root.titleH - (root.maxed ? 0 : 1)

            Label {
                x: 12 * root.u
                width: buttons.x - x - 12 * root.u
                anchors.verticalCenter: parent.verticalCenter
                u: root.u; tok: t; textStyle: t.label
                color: root.active ? t.fg : t.muted
                text: Names.captionFromTitle(decoration.client.caption)
                elide: Text.ElideRight
            }

            Row {
                id: buttons
                anchors.right: parent.right; anchors.rightMargin: 8 * root.u
                anchors.verticalCenter: parent.verticalCenter
                spacing: 10 * root.u
                Repeater {
                    model: [DecorationOptions.DecorationButtonMinimize, DecorationOptions.DecorationButtonMaximizeRestore,
                            DecorationOptions.DecorationButtonClose]
                    DecorationButton {
                        id: btn
                        required property int modelData
                        buttonType: modelData
                        width: 20 * root.u; height: 20 * root.u
                        visible: enabled
                        readonly property color ink: !root.active ? t.muted
                                                     : btn.hovered && buttonType === DecorationOptions.DecorationButtonClose ? t.onStatus : t.fg
                        Rectangle {   // hover: pill well; close goes error pastel with ink (status chip rule)
                            anchors.fill: parent
                            radius: width / 2
                            visible: btn.hovered
                            color: btn.buttonType === DecorationOptions.DecorationButtonClose ? t.error : t.well
                        }
                        readonly property real g: 9 * root.u            // glyph size
                        readonly property real lw: Math.max(1, Math.round(root.u * 0.75))
                        // minimise: one line
                        Rectangle {
                            visible: btn.buttonType === DecorationOptions.DecorationButtonMinimize
                            anchors.centerIn: parent; width: btn.g; height: btn.lw; color: btn.ink
                        }
                        // maximise: square (restore: two offset squares)
                        Rectangle {
                            visible: btn.buttonType === DecorationOptions.DecorationButtonMaximizeRestore
                            anchors.centerIn: parent
                            anchors.horizontalCenterOffset: root.maxed ? -1.5 * root.u : 0
                            anchors.verticalCenterOffset: root.maxed ? 1.5 * root.u : 0
                            width: root.maxed ? btn.g - 2 * root.u : btn.g; height: width
                            color: "transparent"; border.width: btn.lw; border.color: btn.ink
                        }
                        Rectangle {
                            visible: btn.buttonType === DecorationOptions.DecorationButtonMaximizeRestore && root.maxed
                            anchors.centerIn: parent
                            anchors.horizontalCenterOffset: 1.5 * root.u; anchors.verticalCenterOffset: -1.5 * root.u
                            width: btn.g - 2 * root.u; height: width
                            color: "transparent"; border.width: btn.lw; border.color: btn.ink; z: -1
                        }
                        // close: two crossed lines
                        Repeater {
                            model: btn.buttonType === DecorationOptions.DecorationButtonClose ? [45, -45] : []
                            Rectangle {
                                required property real modelData
                                anchors.centerIn: parent; width: btn.g * 1.25; height: btn.lw
                                rotation: modelData; antialiasing: true; color: btn.ink
                            }
                        }
                    }
                }
            }
            Component.onCompleted: decoration.installTitleItem(titleBar)
        }
        // Rule under the title (page 10).
        Rectangle { x: 1; y: root.titleH - 1; width: parent.width - 2; height: 1; color: t.line }
    }
}
