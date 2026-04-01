from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import Text, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import settings_dependency
from app.api.media import _build_media_conditions, _earliest_ordering, _recent_ordering, _row_value
from app.core.config import Settings
from app.db.session import get_db
from app.models import MediaItem
from app.services.dlna import (
    CONNECTION_MANAGER_URN,
    CONTENT_DIRECTORY_URN,
    MEDIA_SERVER_URN,
    ROOT_DEVICE_ST,
    dlna_advertisements,
    dlna_base_url,
    dlna_server_name,
    dlna_udn,
)
from app.services.previews import ensure_preview

router = APIRouter(prefix="/dlna", tags=["dlna"])

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
DIDL_NS = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
DC_NS = "http://purl.org/dc/elements/1.1/"
UPNP_NS = "urn:schemas-upnp-org:metadata-1-0/upnp/"
DLNA_IMAGE_PROTOCOL = "http-get:*:image/jpeg:DLNA.ORG_PN=JPEG_LRG;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=00f00000000000000000000000000000"
DEFAULT_BROWSE_LIMIT = 30
DLNA_EVENT_SID = "uuid:photohunting-event-1"


def _container_id_for_trip(trip_name: str) -> str:
    return f"trip:{quote(trip_name, safe='')}"


def _trip_name_from_container(object_id: str) -> str | None:
    if not object_id.startswith("trip:"):
        return None
    return unquote(object_id.split(":", 1)[1])


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _soap_response(action: str, inner_xml: str, service_type: str) -> Response:
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_NS}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action}Response xmlns:u="{service_type}">{inner_xml}</u:{action}Response>'
        "</s:Body>"
        "</s:Envelope>"
    )
    return Response(content=envelope, media_type='text/xml; charset="utf-8"')


def _soap_fault(code: int, description: str) -> Response:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_NS}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        '<s:Fault>'
        "<faultcode>s:Client</faultcode>"
        "<faultstring>UPnPError</faultstring>"
        "<detail>"
        '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{code}</errorCode>"
        f"<errorDescription>{escape(description)}</errorDescription>"
        "</UPnPError>"
        "</detail>"
        "</s:Fault>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return Response(content=body, media_type='text/xml; charset="utf-8"', status_code=500)


def _parse_soap_action(request: Request, body: bytes) -> tuple[str | None, dict[str, str]]:
    soap_action = request.headers.get("soapaction", "").strip('"')
    action_name = soap_action.split("#", 1)[-1] if "#" in soap_action else None
    values: dict[str, str] = {}
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return action_name, values
    body_node = next((node for node in root.iter() if _strip_ns(node.tag) == "Body"), None)
    if body_node is None or not list(body_node):
        return action_name, values
    action_node = list(body_node)[0]
    if action_name is None:
        action_name = _strip_ns(action_node.tag)
    for child in list(action_node):
        values[_strip_ns(child.tag)] = child.text or ""
    return action_name, values


def _image_media_query(conditions: list):
    return (
        select(
            MediaItem.id,
            MediaItem.filename,
            MediaItem.source_path,
            MediaItem.caption,
            MediaItem.place,
            MediaItem.city,
            MediaItem.country,
            MediaItem.trip_name,
            MediaItem.date_taken,
            MediaItem.analysis_status,
            MediaItem.thumbnail_url,
            MediaItem.width,
            MediaItem.height,
        )
        .where(and_(*conditions))
    )


def _dlna_image_conditions(*, trip_name: str | None = None) -> list:
    return _build_media_conditions(media_type="image", analysis_status="completed", trip_name=trip_name)


def _media_title(row) -> str:
    return _row_value(row, "trip_name") or _row_value(row, "caption") or _row_value(row, "filename") or "Photo"


def _media_res_url(settings: Settings, media_id: str) -> str:
    return f"{dlna_base_url(settings)}/dlna/media/{media_id}.jpg"


def _container_xml(
    object_id: str,
    parent_id: str,
    title: str,
    child_count: int,
    upnp_class: str,
    album_art_url: str | None = None,
) -> str:
    album_art_xml = f"<upnp:albumArtURI>{escape(album_art_url)}</upnp:albumArtURI>" if album_art_url else ""
    return (
        f'<container id="{escape(object_id)}" parentID="{escape(parent_id)}" restricted="1" childCount="{child_count}">'
        f"<dc:title>{escape(title)}</dc:title>"
        f"<upnp:class>{escape(upnp_class)}</upnp:class>"
        f"{album_art_xml}"
        "</container>"
    )


def _item_xml(settings: Settings, row, parent_id: str) -> str:
    media_id = _row_value(row, "id")
    title = _media_title(row)
    filename = _row_value(row, "filename") or media_id
    date_taken = _row_value(row, "date_taken")
    width = _row_value(row, "width")
    height = _row_value(row, "height")
    resolution = f'{width}x{height}' if width and height else None
    res_attrs = [f'protocolInfo="{DLNA_IMAGE_PROTOCOL}"']
    if resolution:
        res_attrs.append(f'resolution="{resolution}"')
    date_xml = ""
    if isinstance(date_taken, datetime):
        date_xml = f"<dc:date>{date_taken.isoformat()}</dc:date>"
    album_xml = ""
    if _row_value(row, "trip_name"):
        album_xml = f"<upnp:album>{escape(_row_value(row, 'trip_name'))}</upnp:album>"
    return (
        f'<item id="media:{escape(media_id)}" parentID="{escape(parent_id)}" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        f"<upnp:class>object.item.imageItem.photo</upnp:class>"
        f"<dc:creator>Photo Hunting</dc:creator>"
        f"{date_xml}"
        f"{album_xml}"
        f'<res {" ".join(res_attrs)}>{escape(_media_res_url(settings, media_id))}</res>'
        f"<upnp:icon>{escape(_media_res_url(settings, media_id))}</upnp:icon>"
        f"<upnp:originalTrackNumber>1</upnp:originalTrackNumber>"
        f"<dc:description>{escape(filename)}</dc:description>"
        "</item>"
    )


def _browse_root(db: Session, settings: Settings) -> tuple[list[str], int]:
    recent_total = _recent_total(db, settings)
    trip_total = _trip_total(db)
    containers = [
        _container_xml("recent", "0", "Recently Added", recent_total, "object.container.album.photoAlbum"),
        _container_xml("trips", "0", "Trips", trip_total, "object.container"),
    ]
    return containers, len(containers)


def _recent_total(db: Session, settings: Settings) -> int:
    conditions = _dlna_image_conditions()
    actual_total = db.scalar(select(func.count(MediaItem.id)).where(and_(*conditions))) or 0
    return min(actual_total, settings.dlna_recent_window_size)


def _trip_total(db: Session) -> int:
    conditions = _dlna_image_conditions()
    return db.scalar(
        select(func.count(func.distinct(MediaItem.trip_name))).where(and_(*conditions), MediaItem.trip_name.is_not(None))
    ) or 0


def _browse_recent(
    db: Session,
    settings: Settings,
    *,
    start: int,
    count: int,
) -> tuple[list[str], int]:
    conditions = _dlna_image_conditions()
    total = _recent_total(db, settings)
    if start >= total:
        return [], total
    rows = db.execute(
        _image_media_query(conditions)
        .order_by(*_recent_ordering())
        .offset(start)
        .limit(min(count, total - start))
    ).all()
    return [_item_xml(settings, row, "recent") for row in rows], total


def _trip_cover_urls(db: Session, settings: Settings, trip_names: list[str]) -> dict[str, str]:
    if not trip_names:
        return {}
    conditions = _dlna_image_conditions()
    cover_subquery = (
        select(
            MediaItem.trip_name.label("trip_name"),
            MediaItem.id.label("id"),
            func.row_number()
            .over(partition_by=MediaItem.trip_name, order_by=_earliest_ordering())
            .label("row_number"),
        )
        .where(and_(*conditions), MediaItem.trip_name.in_(trip_names))
        .subquery()
    )
    rows = db.execute(
        select(cover_subquery.c.trip_name, cover_subquery.c.id).where(cover_subquery.c.row_number == 1)
    ).all()
    return {
        _row_value(row, "trip_name"): _media_res_url(settings, _row_value(row, "id"))
        for row in rows
        if _row_value(row, "trip_name") and _row_value(row, "id")
    }


def _browse_trip_containers(db: Session, settings: Settings, *, start: int, count: int) -> tuple[list[str], int]:
    conditions = _dlna_image_conditions()
    latest_date_expr = func.max(func.coalesce(MediaItem.date_taken, MediaItem.indexed_at))
    rows = db.execute(
        select(
            MediaItem.trip_name.label("trip_name"),
            func.count(MediaItem.id).label("count"),
            latest_date_expr.label("latest_date"),
        )
        .where(and_(*conditions), MediaItem.trip_name.is_not(None))
        .group_by(MediaItem.trip_name)
        .order_by(latest_date_expr.desc(), MediaItem.trip_name.asc())
        .offset(start)
        .limit(count)
    ).all()
    total = _trip_total(db)
    trip_names = [row.trip_name for row in rows if row.trip_name]
    cover_urls = _trip_cover_urls(db, settings, trip_names)
    containers = [
        _container_xml(
            _container_id_for_trip(row.trip_name),
            "trips",
            row.trip_name,
            row.count,
            "object.container.album.photoAlbum",
            cover_urls.get(row.trip_name),
        )
        for row in rows
        if row.trip_name
    ]
    return containers, total


def _browse_trip_items(
    db: Session,
    settings: Settings,
    *,
    trip_name: str,
    start: int,
    count: int,
) -> tuple[list[str], int]:
    conditions = _dlna_image_conditions(trip_name=trip_name)
    total = db.scalar(select(func.count(MediaItem.id)).where(and_(*conditions))) or 0
    rows = db.execute(
        _image_media_query(conditions)
        .order_by(
            case((MediaItem.date_taken.is_(None), 1), else_=0),
            MediaItem.date_taken.asc(),
            MediaItem.indexed_at.asc(),
        )
        .offset(start)
        .limit(count)
    ).all()
    return [_item_xml(settings, row, _container_id_for_trip(trip_name)) for row in rows], total


def _metadata_xml_for_object(db: Session, settings: Settings, object_id: str) -> tuple[str, int] | None:
    if object_id == "0":
        return _container_xml("0", "-1", dlna_server_name(settings), 2, "object.container"), 1
    if object_id == "recent":
        return _container_xml("recent", "0", "Recently Added", _recent_total(db, settings), "object.container.album.photoAlbum"), 1
    if object_id == "trips":
        return _container_xml("trips", "0", "Trips", _trip_total(db), "object.container"), 1
    trip_name = _trip_name_from_container(object_id)
    if trip_name is not None:
        total = db.scalar(
            select(func.count(MediaItem.id)).where(and_(*_dlna_image_conditions(trip_name=trip_name)))
        ) or 0
        cover_url = _trip_cover_urls(db, settings, [trip_name]).get(trip_name)
        return _container_xml(
            object_id,
            "trips",
            trip_name,
            total,
            "object.container.album.photoAlbum",
            cover_url,
        ), 1
    if object_id.startswith("media:"):
        media_id = object_id.split(":", 1)[1]
        row = db.execute(
            _image_media_query([MediaItem.deleted_at.is_(None), MediaItem.id == media_id])
        ).first()
        if row is None:
            return None
        parent_id = _container_id_for_trip(_row_value(row, "trip_name")) if _row_value(row, "trip_name") else "recent"
        return _item_xml(settings, row, parent_id), 1
    return None


def _didl(result_nodes: Iterable[str]) -> str:
    return (
        f'<DIDL-Lite xmlns="{DIDL_NS}" xmlns:dc="{DC_NS}" xmlns:upnp="{UPNP_NS}">'
        f'{"".join(result_nodes)}'
        "</DIDL-Lite>"
    )


def _content_directory_browse(db: Session, settings: Settings, values: dict[str, str]) -> Response:
    object_id = values.get("ObjectID", "0")
    browse_flag = values.get("BrowseFlag", "BrowseDirectChildren")
    starting_index = int(values.get("StartingIndex", "0") or 0)
    requested_count = int(values.get("RequestedCount", "0") or 0)
    limit = min(requested_count or DEFAULT_BROWSE_LIMIT, settings.dlna_content_limit)

    if browse_flag == "BrowseMetadata":
        metadata = _metadata_xml_for_object(db, settings, object_id)
        if metadata is None:
            return _soap_fault(701, "No such object")
        result_xml, total = metadata
        return _soap_response(
            "Browse",
            (
                f"<Result>{escape(_didl([result_xml]))}</Result>"
                "<NumberReturned>1</NumberReturned>"
                f"<TotalMatches>{total}</TotalMatches>"
                "<UpdateID>1</UpdateID>"
            ),
            CONTENT_DIRECTORY_URN,
        )

    if object_id == "0":
        nodes, total = _browse_root(db, settings)
    elif object_id == "recent":
        nodes, total = _browse_recent(db, settings, start=starting_index, count=limit)
    elif object_id == "trips":
        nodes, total = _browse_trip_containers(db, settings, start=starting_index, count=limit)
    else:
        trip_name = _trip_name_from_container(object_id)
        if trip_name is None:
            return _soap_fault(701, "No such object")
        nodes, total = _browse_trip_items(db, settings, trip_name=trip_name, start=starting_index, count=limit)

    number_returned = len(nodes)
    return _soap_response(
        "Browse",
        (
            f"<Result>{escape(_didl(nodes))}</Result>"
            f"<NumberReturned>{number_returned}</NumberReturned>"
            f"<TotalMatches>{total}</TotalMatches>"
            "<UpdateID>1</UpdateID>"
        ),
        CONTENT_DIRECTORY_URN,
    )


def _content_directory_scpd() -> str:
    return f"""<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action><name>Browse</name><argumentList>
      <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
      <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
      <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
      <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
      <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
      <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
      <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
      <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
      <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
      <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetSearchCapabilities</name><argumentList>
      <argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetSortCapabilities</name><argumentList>
      <argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetSystemUpdateID</name><argumentList>
      <argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
    </argumentList></action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""


def _connection_manager_scpd() -> str:
    return """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action><name>GetProtocolInfo</name><argumentList>
      <argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>
      <argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetCurrentConnectionIDs</name><argumentList>
      <argument><name>ConnectionIDs</name><direction>out</direction><relatedStateVariable>CurrentConnectionIDs</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetCurrentConnectionInfo</name><argumentList>
      <argument><name>ConnectionID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
      <argument><name>RcsID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_RcsID</relatedStateVariable></argument>
      <argument><name>AVTransportID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_AVTransportID</relatedStateVariable></argument>
      <argument><name>ProtocolInfo</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ProtocolInfo</relatedStateVariable></argument>
      <argument><name>PeerConnectionManager</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionManager</relatedStateVariable></argument>
      <argument><name>PeerConnectionID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
      <argument><name>Direction</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Direction</relatedStateVariable></argument>
      <argument><name>Status</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionStatus</relatedStateVariable></argument>
    </argumentList></action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>CurrentConnectionIDs</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_RcsID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_AVTransportID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionManager</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Direction</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionStatus</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""


@router.get("/device.xml", include_in_schema=False)
def dlna_device_description(settings: Settings = Depends(settings_dependency)):
    base_url = dlna_base_url(settings)
    device_type = MEDIA_SERVER_URN
    udn = dlna_udn(settings)
    xml = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>{escape(base_url)}/</URLBase>
  <device>
    <deviceType>{device_type}</deviceType>
    <friendlyName>{escape(dlna_server_name(settings))}</friendlyName>
    <manufacturer>Photo Hunting</manufacturer>
    <manufacturerURL>https://github.com/FelixLee888/PhotoHunting</manufacturerURL>
    <modelDescription>Photo Hunting DLNA server</modelDescription>
    <modelName>Photo Hunting MediaServer</modelName>
    <modelNumber>1</modelNumber>
    <serialNumber>photohunting</serialNumber>
    <UDN>{udn}</UDN>
    <serviceList>
      <service>
        <serviceType>{CONTENT_DIRECTORY_URN}</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <controlURL>/dlna/content-directory/control</controlURL>
        <eventSubURL>/dlna/content-directory/event</eventSubURL>
        <SCPDURL>/dlna/content-directory/scpd.xml</SCPDURL>
      </service>
      <service>
        <serviceType>{CONNECTION_MANAGER_URN}</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <controlURL>/dlna/connection-manager/control</controlURL>
        <eventSubURL>/dlna/connection-manager/event</eventSubURL>
        <SCPDURL>/dlna/connection-manager/scpd.xml</SCPDURL>
      </service>
    </serviceList>
  </device>
</root>"""
    return Response(content=xml, media_type="text/xml")


@router.get("/content-directory/scpd.xml", include_in_schema=False)
def dlna_content_directory_scpd():
    return Response(content=_content_directory_scpd(), media_type="text/xml")


@router.get("/connection-manager/scpd.xml", include_in_schema=False)
def dlna_connection_manager_scpd():
    return Response(content=_connection_manager_scpd(), media_type="text/xml")


@router.api_route("/content-directory/event", methods=["SUBSCRIBE", "UNSUBSCRIBE"], include_in_schema=False)
@router.api_route("/connection-manager/event", methods=["SUBSCRIBE", "UNSUBSCRIBE"], include_in_schema=False)
def dlna_event_stub():
    return Response(headers={"SID": DLNA_EVENT_SID, "TIMEOUT": "Second-1800"})


@router.post("/content-directory/control", include_in_schema=False)
async def dlna_content_directory_control(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dependency),
):
    body = await request.body()
    action, values = _parse_soap_action(request, body)
    if action == "Browse":
        return _content_directory_browse(db, settings, values)
    if action == "GetSearchCapabilities":
        return _soap_response("GetSearchCapabilities", "<SearchCaps></SearchCaps>", CONTENT_DIRECTORY_URN)
    if action == "GetSortCapabilities":
        return _soap_response("GetSortCapabilities", "<SortCaps></SortCaps>", CONTENT_DIRECTORY_URN)
    if action == "GetSystemUpdateID":
        return _soap_response("GetSystemUpdateID", "<Id>1</Id>", CONTENT_DIRECTORY_URN)
    return _soap_fault(401, "Invalid Action")


@router.post("/connection-manager/control", include_in_schema=False)
async def dlna_connection_manager_control(request: Request):
    body = await request.body()
    action, values = _parse_soap_action(request, body)
    if action == "GetProtocolInfo":
        return _soap_response(
            "GetProtocolInfo",
            f"<Source>{escape(DLNA_IMAGE_PROTOCOL)}</Source><Sink></Sink>",
            CONNECTION_MANAGER_URN,
        )
    if action == "GetCurrentConnectionIDs":
        return _soap_response("GetCurrentConnectionIDs", "<ConnectionIDs></ConnectionIDs>", CONNECTION_MANAGER_URN)
    if action == "GetCurrentConnectionInfo":
        connection_id = escape(values.get("ConnectionID", "-1"))
        return _soap_response(
            "GetCurrentConnectionInfo",
            (
                "<RcsID>-1</RcsID>"
                "<AVTransportID>-1</AVTransportID>"
                f"<ProtocolInfo>{escape(DLNA_IMAGE_PROTOCOL)}</ProtocolInfo>"
                "<PeerConnectionManager></PeerConnectionManager>"
                f"<PeerConnectionID>{connection_id}</PeerConnectionID>"
                "<Direction>Output</Direction>"
                "<Status>OK</Status>"
            ),
            CONNECTION_MANAGER_URN,
        )
    return _soap_fault(401, "Invalid Action")


@router.api_route("/media/{media_id}.jpg", methods=["GET", "HEAD"], include_in_schema=False)
def dlna_media(
    media_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dependency),
):
    item = db.get(MediaItem, media_id)
    if not item or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Media item not found.")
    preview_path = ensure_preview(item, settings)
    if not preview_path:
        raise HTTPException(status_code=404, detail="Preview image is not available.")
    return FileResponse(
        preview_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=86400",
            "contentFeatures.dlna.org": DLNA_IMAGE_PROTOCOL.split(":", 3)[-1],
            "transferMode.dlna.org": "Interactive",
        },
    )
