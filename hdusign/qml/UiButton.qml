import QtQuick
import QtQuick.Controls

AbstractButton {
    id: control
    property string iconName: ""
    property bool danger: false
    property bool primary: false
    property bool selected: false
    property bool alignLeft: false
    property bool outline: false
    property int fontPixelSize: 14
    property string tooltip: ""
    property color ink: danger ? Theme.red : primary ? "#ffffff" : Theme.muted
    implicitHeight: 32
    implicitWidth: Math.max(32, label.implicitWidth + (iconName ? 26 : 0) + 20)
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: tooltip || text
    opacity: enabled ? 1 : 0.45
    background: Rectangle {
        radius: 4
        color: control.primary ? (control.down || control.hovered ? Theme.accentHover : Theme.accent)
               : control.down || control.hovered ? (control.danger ? Theme.redFill : Theme.hover)
               : control.selected ? Theme.selected : "transparent"
        border.width: control.danger || control.outline || control.visualFocus ? 1 : 0
        border.color: control.visualFocus ? Theme.muted : control.danger ? Theme.redBorder : Theme.outline
    }
    contentItem: Item {
        Row {
            anchors.verticalCenter: parent.verticalCenter
            x: control.alignLeft ? 10 : (parent.width - width) / 2
            spacing: 8
            Icon { visible: control.iconName !== ""; name: control.iconName; ink: control.ink; width: 16; anchors.verticalCenter: parent.verticalCenter }
            UiText {
                id: label; text: control.text; visible: text.length > 0
                width: Math.min(implicitWidth, Math.max(0, control.width - 20 - (control.iconName ? 24 : 0)))
                elide: Text.ElideRight; color: control.ink
                font.pixelSize: control.fontPixelSize; anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
    ToolTip.visible: hovered && tooltip.length > 0
    ToolTip.text: tooltip
    ToolTip.delay: 650
}
