import QtQuick

// One Notion page property: fixed-width muted name, value aligned beside it,
// and a soft hover wash across the row.
Item {
    id: prop
    property string iconName: "status"
    property string name: ""
    default property alias content: value.data
    width: parent.width
    height: 30
    HoverHandler { id: hover }
    Rectangle { anchors.fill: parent; radius: 4; color: hover.hovered ? Theme.hover : "transparent" }
    Row {
        x: 6; width: 136; spacing: 8; anchors.verticalCenter: parent.verticalCenter
        Icon { name: prop.iconName; width: 15; anchors.verticalCenter: parent.verticalCenter }
        UiText { text: prop.name; color: Theme.muted; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
    }
    Item {
        id: value
        x: 148; width: parent.width - x - 6; height: parent.height
    }
}
