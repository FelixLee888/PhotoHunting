from __future__ import annotations

import logging
import socket
import struct
import threading
from dataclasses import dataclass
from email.utils import formatdate
from uuid import NAMESPACE_URL, uuid5

from app.core.config import Settings

logger = logging.getLogger(__name__)

SSDP_MULTICAST_HOST = "239.255.255.250"
CONTENT_DIRECTORY_URN = "urn:schemas-upnp-org:service:ContentDirectory:1"
CONNECTION_MANAGER_URN = "urn:schemas-upnp-org:service:ConnectionManager:1"
MEDIA_SERVER_URN = "urn:schemas-upnp-org:device:MediaServer:1"
ROOT_DEVICE_ST = "upnp:rootdevice"


def detect_dlna_host(settings: Settings) -> str:
    if settings.dlna_advertise_host:
        return settings.dlna_advertise_host
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        host = probe.getsockname()[0]
        if host:
            return host
    except OSError:
        pass
    finally:
        probe.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def dlna_udn(settings: Settings) -> str:
    host = detect_dlna_host(settings)
    stable_uuid = uuid5(NAMESPACE_URL, f"photohunting-dlna:{host}:{settings.dlna_port}")
    return f"uuid:{stable_uuid}"


def dlna_base_url(settings: Settings) -> str:
    return f"http://{detect_dlna_host(settings)}:{settings.dlna_port}"


def dlna_server_name(settings: Settings) -> str:
    host = detect_dlna_host(settings)
    return f"{settings.dlna_friendly_name} on {host}"


@dataclass(slots=True)
class SSDPAdvertisement:
    nt: str
    usn: str


def dlna_advertisements(settings: Settings) -> list[SSDPAdvertisement]:
    udn = dlna_udn(settings)
    return [
        SSDPAdvertisement(nt=ROOT_DEVICE_ST, usn=f"{udn}::{ROOT_DEVICE_ST}"),
        SSDPAdvertisement(nt=udn, usn=udn),
        SSDPAdvertisement(nt=MEDIA_SERVER_URN, usn=f"{udn}::{MEDIA_SERVER_URN}"),
        SSDPAdvertisement(nt=CONTENT_DIRECTORY_URN, usn=f"{udn}::{CONTENT_DIRECTORY_URN}"),
        SSDPAdvertisement(nt=CONNECTION_MANAGER_URN, usn=f"{udn}::{CONNECTION_MANAGER_URN}"),
    ]


class SSDPServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.listener: socket.socket | None = None
        self.sender: socket.socket | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        try:
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind(("", self.settings.dlna_ssdp_port))
            membership = struct.pack(
                "4s4s",
                socket.inet_aton(SSDP_MULTICAST_HOST),
                socket.inet_aton("0.0.0.0"),
            )
            self.listener.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            self.listener.settimeout(1.0)
            self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            self.thread = threading.Thread(target=self._serve, name="photohunting-ssdp", daemon=True)
            self.thread.start()
            logger.info("Started SSDP responder for DLNA at %s", dlna_base_url(self.settings))
        except OSError as exc:
            logger.warning("Unable to start SSDP responder: %s", exc)
            self.close()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        try:
            self._send_notify("ssdp:byebye")
        except OSError:
            pass
        self.close()

    def close(self) -> None:
        for sock in (self.listener, self.sender):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self.listener = None
        self.sender = None
        self.thread = None

    def _serve(self) -> None:
        next_notify = 0.0
        while not self.stop_event.is_set():
            if next_notify <= 0:
                try:
                    self._send_notify("ssdp:alive")
                except OSError as exc:
                    logger.debug("DLNA notify failed: %s", exc)
                next_notify = float(self.settings.dlna_notify_interval_seconds)
            try:
                assert self.listener is not None
                payload, address = self.listener.recvfrom(4096)
            except socket.timeout:
                next_notify = max(0.0, next_notify - 1.0)
                continue
            except OSError:
                break
            next_notify = max(0.0, next_notify - 0.1)
            self._handle_request(payload.decode("utf-8", "ignore"), address)

    def _send_notify(self, nts: str) -> None:
        if self.sender is None:
            return
        location = f"{dlna_base_url(self.settings)}/dlna/device.xml"
        for advertisement in dlna_advertisements(self.settings):
            message = (
                "NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_MULTICAST_HOST}:{self.settings.dlna_ssdp_port}\r\n"
                f"CACHE-CONTROL: max-age={self.settings.dlna_cache_max_age_seconds}\r\n"
                f"LOCATION: {location}\r\n"
                f"NT: {advertisement.nt}\r\n"
                f"NTS: {nts}\r\n"
                f"SERVER: PhotoHunting/1.0 UPnP/1.0 DLNADOC/1.50\r\n"
                f"USN: {advertisement.usn}\r\n"
                "\r\n"
            )
            self.sender.sendto(message.encode("utf-8"), (SSDP_MULTICAST_HOST, self.settings.dlna_ssdp_port))

    def _handle_request(self, payload: str, address: tuple[str, int]) -> None:
        if "M-SEARCH * HTTP/1.1" not in payload.upper():
            return
        headers = {}
        for line in payload.split("\r\n")[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().upper()] = value.strip()
        if headers.get("MAN", "").strip('"').lower() != "ssdp:discover":
            return
        search_target = headers.get("ST")
        if not search_target:
            return
        for advertisement in dlna_advertisements(self.settings):
            if search_target not in {"ssdp:all", advertisement.nt}:
                continue
            self._send_search_response(search_target if search_target != "ssdp:all" else advertisement.nt, advertisement.usn, address)

    def _send_search_response(self, st: str, usn: str, address: tuple[str, int]) -> None:
        if self.sender is None:
            return
        location = f"{dlna_base_url(self.settings)}/dlna/device.xml"
        message = (
            "HTTP/1.1 200 OK\r\n"
            f"CACHE-CONTROL: max-age={self.settings.dlna_cache_max_age_seconds}\r\n"
            f"DATE: {formatdate(usegmt=True)}\r\n"
            "EXT:\r\n"
            f"LOCATION: {location}\r\n"
            f"SERVER: PhotoHunting/1.0 UPnP/1.0 DLNADOC/1.50\r\n"
            f"ST: {st}\r\n"
            f"USN: {usn}\r\n"
            "\r\n"
        )
        self.sender.sendto(message.encode("utf-8"), address)


class DLNAManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ssdp = SSDPServer(settings)

    def start(self) -> None:
        if not self.settings.dlna_enabled:
            return
        self.ssdp.start()

    def stop(self) -> None:
        self.ssdp.stop()
