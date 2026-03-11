from __future__ import annotations

import socket
import struct
import threading
import time

import numpy as np

from .config import PreviewConfig


ARTNET_ID = b"Art-Net\x00"
OP_OUTPUT = 0x5000
OP_SYNC = 0x5200
PIXELS_PER_SUBNET = 170


class ArtnetPreviewService:
    def __init__(self, config: PreviewConfig) -> None:
        self.config = config
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = np.zeros((config.rows, config.cols, 3), dtype=np.uint8)
        self.pending_frame = np.zeros((config.rows, config.cols, 3), dtype=np.uint8)
        self.last_dmx_ts = 0.0
        self.last_sync_ts = 0.0
        self.dmx_packets = 0
        self.sync_packets = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((config.bind_host, int(config.port)))
        self.sock.settimeout(0.25)
        self.listener_thread = threading.Thread(target=self._listen_loop, name="artnet-preview", daemon=True)
        self.listener_thread.start()

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.sock.close()
        except OSError:
            pass
        self.listener_thread.join(timeout=1.0)

    def get_snapshot(self) -> tuple[np.ndarray, dict[str, float | int | bool]]:
        with self.lock:
            frame = self.frame.copy()
            last_dmx_ts = self.last_dmx_ts
            last_sync_ts = self.last_sync_ts
            dmx_packets = self.dmx_packets
            sync_packets = self.sync_packets

        now = time.time()
        return frame, {
            "dmx_packets": dmx_packets,
            "sync_packets": sync_packets,
            "last_dmx_age_s": (now - last_dmx_ts) if last_dmx_ts else -1.0,
            "last_sync_age_s": (now - last_sync_ts) if last_sync_ts else -1.0,
            "has_signal": last_dmx_ts > 0.0 and (now - last_dmx_ts) < 2.0,
            "use_sync": self.config.use_sync,
            "cols": self.config.cols,
            "rows": self.config.rows,
        }

    def _listen_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                packet, _addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break

            op, payload = _parse_artnet(packet)
            if op is None:
                continue
            if op == OP_SYNC:
                with self.lock:
                    self.sync_packets += 1
                    self.last_sync_ts = time.time()
                    if self.config.use_sync:
                        self.frame[:, :, :] = self.pending_frame
                continue

            net, subnet, _port_universe, dmx = payload
            if net != self.config.net:
                continue

            start_pixel = subnet * PIXELS_PER_SUBNET
            max_pixels = self.config.cols * self.config.rows
            if start_pixel >= max_pixels:
                continue

            pixel_count = min(len(dmx) // 3, max_pixels - start_pixel)
            if pixel_count <= 0:
                continue

            rgb = np.frombuffer(dmx, dtype=np.uint8, count=pixel_count * 3).reshape(pixel_count, 3)

            with self.lock:
                flat = self.pending_frame.reshape((-1, 3))
                flat[start_pixel : start_pixel + pixel_count] = rgb
                if not self.config.use_sync:
                    self.frame[:, :, :] = self.pending_frame
                self.dmx_packets += 1
                self.last_dmx_ts = time.time()


def _parse_artnet(packet: bytes) -> tuple[int | None, tuple[int, int, int, bytes] | None]:
    if len(packet) < 10 or packet[0:8] != ARTNET_ID:
        return None, None
    op = struct.unpack("<H", packet[8:10])[0]
    if op == OP_SYNC:
        return OP_SYNC, None
    if op != OP_OUTPUT or len(packet) < 18:
        return None, None
    subuni = packet[14]
    net = packet[15]
    port_universe = subuni & 0x0F
    subnet = (subuni >> 4) & 0x0F
    length = struct.unpack(">H", packet[16:18])[0]
    return OP_OUTPUT, (net, subnet, port_universe, packet[18 : 18 + length])
