from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import socket
import struct
import threading
import time
from typing import Callable

from .config import MidiDeviceConfig


@dataclass(slots=True)
class MidiShortMessage:
    status: int
    data1: int
    data2: int

    @property
    def channel(self) -> int:
        return self.status & 0x0F

    @property
    def kind(self) -> int:
        return self.status & 0xF0


class NetworkMidiDevice:
    def __init__(
        self,
        config: MidiDeviceConfig,
        on_short_message: Callable[[MidiShortMessage], None] | None = None,
    ) -> None:
        self.config = config
        self.on_short_message = on_short_message
        self.stop_event = threading.Event()
        self.send_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.short_out_messages = 0
        self.short_in_messages = 0
        self.out_packets = 0
        self.in_packets = 0
        self.last_rx_ts = 0.0
        self.last_tx_ts = 0.0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((config.bind_host, int(config.port)))
        self.sock.settimeout(0.25)

        if _is_multicast(config.target_host):
            membership_host = config.bind_host if config.bind_host != "0.0.0.0" else "0.0.0.0"
            membership = socket.inet_aton(config.target_host) + socket.inet_aton(membership_host)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

        self.listener_thread = threading.Thread(target=self._listen_loop, name=f"{config.name}-midi", daemon=True)
        self.listener_thread.start()

        self.heartbeat_thread: threading.Thread | None = None
        if config.send_heartbeat and config.heartbeat_target_host:
            self.heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"{config.name}-heartbeat",
                daemon=True,
            )
            self.heartbeat_thread.start()

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.sock.close()
        except OSError:
            pass
        self.listener_thread.join(timeout=1.0)
        if self.heartbeat_thread is not None:
            self.heartbeat_thread.join(timeout=1.0)

    def send_cc(self, cc_num: int, value: int, channel: int | None = None) -> None:
        status = 0xB0 | ((self.config.channel if channel is None else channel) & 0x0F)
        self.send_short_message(status, cc_num, value)

    def send_note(self, note_num: int, value: int, channel: int | None = None) -> None:
        status = 0x90 | ((self.config.channel if channel is None else channel) & 0x0F)
        self.send_short_message(status, note_num, value)

    def send_short_message(self, status: int, data1: int, data2: int) -> None:
        packet = bytes((status & 0xFF, data1 & 0x7F, data2 & 0x7F))
        with self.send_lock:
            self.sock.sendto(packet, (self.config.target_host, int(self.config.port)))
        now = time.time()
        with self.stats_lock:
            self.short_out_messages += 1
            self.out_packets += 1
            self.last_tx_ts = now

    def get_status(self) -> dict[str, float | int | str | bool]:
        now = time.time()
        with self.stats_lock:
            age = (now - self.last_rx_ts) if self.last_rx_ts else -1.0
            return {
                "name": self.config.name,
                "bind_host": self.config.bind_host,
                "target_host": self.config.target_host,
                "port": int(self.config.port),
                "heartbeat_name": self.config.heartbeat_name,
                "short_out_messages": self.short_out_messages,
                "short_in_messages": self.short_in_messages,
                "out_packets": self.out_packets,
                "in_packets": self.in_packets,
                "last_rx_age_s": age,
                "last_tx_ts": self.last_tx_ts,
                "midi_connected": not self.stop_event.is_set(),
            }

    def _listen_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                packet, _addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            now = time.time()
            with self.stats_lock:
                self.in_packets += 1
                self.last_rx_ts = now

            if not packet:
                continue
            if packet[0] == 0xF0:
                continue
            if len(packet) % 3 != 0:
                continue

            for offset in range(0, len(packet), 3):
                status, data1, data2 = struct.unpack_from("BBB", packet, offset)
                if status < 0x80:
                    continue
                with self.stats_lock:
                    self.short_in_messages += 1
                if self.on_short_message is not None:
                    self.on_short_message(MidiShortMessage(status, data1, data2))

    def _heartbeat_loop(self) -> None:
        interval = max(0.25, float(self.config.heartbeat_interval_s))
        while not self.stop_event.wait(interval):
            status = self.get_status()
            payload = {
                "type": "heartbeat",
                "name": self.config.heartbeat_name,
                "bridge_name": self.config.name,
                "target_host": self.config.target_host,
                "target_port": int(self.config.port),
                "usb_to_net_packets": status["short_out_messages"],
                "net_to_usb_packets": status["short_in_messages"],
                "midi_connected": status["midi_connected"],
                "ts": time.time(),
            }
            packet = json.dumps(payload).encode("utf-8")
            with self.send_lock:
                try:
                    self.sock.sendto(packet, (self.config.heartbeat_target_host, int(self.config.heartbeat_port)))
                except OSError:
                    break


def _is_multicast(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_multicast
    except ValueError:
        return False
