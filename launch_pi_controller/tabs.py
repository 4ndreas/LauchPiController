from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import math
from typing import TYPE_CHECKING, Any

import pygame

from .launchcontrol_mapping import (
    ARROW_CCS,
    ROW1_KNOB_CCS,
    ROW2_KNOB_CCS,
    TRACK_BUTTON_NOTES,
    arrow_key,
    knob_key,
    launch_control_led_color,
    track_key,
)
from .network_midi import MidiShortMessage

if TYPE_CHECKING:
    from .app import LaunchPiControllerApp


TAB_BACKGROUND = (12, 16, 24)
PANEL_BACKGROUND = (24, 31, 42)
PANEL_ALT = (28, 36, 49)
TEXT_PRIMARY = (228, 233, 239)
TEXT_MUTED = (140, 150, 165)
ACCENT = (91, 196, 191)
ACCENT_ALT = (245, 166, 72)
WARNING = (232, 100, 82)
SUCCESS = (122, 201, 102)
_FONT_CACHE: dict[tuple[str, int, bool], pygame.font.Font] = {}


class BaseTab:
    title = "Tab"

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        raise NotImplementedError

    def handle_pointer_down(self, app: LaunchPiControllerApp, pos: tuple[int, int]) -> None:
        return None

    def handle_pointer_up(self, app: LaunchPiControllerApp, pos: tuple[int, int]) -> None:
        return None

    def handle_pointer_motion(
        self,
        app: LaunchPiControllerApp,
        pos: tuple[int, int],
        rel: tuple[int, int],
        buttons: tuple[int, int, int],
    ) -> None:
        return None


class PreviewTab(BaseTab):
    title = "Preview"

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        _draw_panel(surface, rect, "Art-Net Preview", "Live input from the configured Art-Net universe")
        inner = rect.inflate(-24, -24)
        preview_rect = pygame.Rect(inner.x, inner.y + 48, inner.width, inner.height - 48)
        preview_rect.height -= 74

        if app.preview_service is None:
            _draw_center_notice(surface, preview_rect, "Preview offline", app.preview_error or "Preview service is not running")
            return

        frame, stats = app.preview_service.get_snapshot()
        rows = int(stats["rows"])
        cols = int(stats["cols"])
        aspect = cols / max(1, rows)
        target_w = preview_rect.width
        target_h = int(target_w / aspect)
        if target_h > preview_rect.height:
            target_h = preview_rect.height
            target_w = int(target_h * aspect)
        scaled_rect = pygame.Rect(0, 0, target_w, target_h)
        scaled_rect.center = preview_rect.center

        arr = frame.transpose((1, 0, 2))
        small = pygame.surfarray.make_surface(arr)
        scaled = pygame.transform.scale(small, scaled_rect.size)
        surface.blit(scaled, scaled_rect.topleft)
        pygame.draw.rect(surface, (49, 59, 74), scaled_rect, 2, border_radius=16)

        if not stats["has_signal"]:
            overlay = pygame.Surface(scaled_rect.size, pygame.SRCALPHA)
            overlay.fill((6, 8, 12, 170))
            surface.blit(overlay, scaled_rect.topleft)
            _draw_center_notice(surface, scaled_rect, "No signal", "Waiting for Art-Net frames")

        footer = pygame.Rect(inner.x, rect.bottom - 76, inner.width, 56)
        _draw_info_badge(surface, footer, f"{cols} x {rows}", ACCENT)
        _draw_info_badge(surface, footer.move(145, 0), f"DMX {int(stats['dmx_packets'])}", ACCENT_ALT)
        _draw_info_badge(surface, footer.move(310, 0), f"Sync {int(stats['sync_packets'])}", SUCCESS)
        if stats["last_dmx_age_s"] >= 0:
            age_text = f"{stats['last_dmx_age_s']:.1f}s ago"
        else:
            age_text = "never"
        _draw_info_badge(surface, footer.move(475, 0), f"Last frame {age_text}", (110, 132, 242))


@dataclass(slots=True)
class DragState:
    key: str
    start_y: int
    start_value: int


class EffectTab(BaseTab):
    title = "Effect"

    def __init__(self) -> None:
        self.knob_values = {knob_key(row, idx): 0 for row in (1, 2) for idx in range(8)}
        self.track_leds = {track_key(idx): 0 for idx in range(8)}
        self.arrow_leds = {arrow_key(name): 0 for name in ARROW_CCS}
        self.hitboxes: list[tuple[str, str, pygame.Rect]] = []
        self.drag_state: DragState | None = None
        self.active_button: tuple[str, str] | None = None

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        _draw_panel(surface, rect, "Effect Controller", "Launch Control-compatible CC/NOTE I/O over UDP")
        inner = rect.inflate(-24, -24)
        status = app.effect_device.get_status() if app.effect_device is not None else None
        status_line = "Device offline"
        status_color = WARNING
        if status is not None:
            age = float(status["last_rx_age_s"])
            if age >= 0 and age < 5:
                status_line = f"Feedback active on {status['target_host']}:{status['port']}"
                status_color = SUCCESS
            else:
                status_line = f"Sending to {status['target_host']}:{status['port']}"
                status_color = ACCENT_ALT
        elif app.effect_error:
            status_line = app.effect_error
        _draw_info_badge(surface, pygame.Rect(inner.x, inner.y, 360, 34), status_line, status_color)

        control_area = pygame.Rect(inner.x, inner.y + 56, inner.width, inner.height - 138)
        strip_width = max(96, (control_area.width - 56) // 8)
        knob_radius = max(28, min(42, strip_width // 3))
        spacing = (control_area.width - strip_width * 8) // 9

        self.hitboxes = []
        for idx in range(8):
            strip_x = control_area.x + spacing + idx * (strip_width + spacing)
            strip_rect = pygame.Rect(strip_x, control_area.y, strip_width, control_area.height)
            pygame.draw.rect(surface, PANEL_ALT, strip_rect, border_radius=24)
            pygame.draw.rect(surface, (48, 61, 77), strip_rect, 2, border_radius=24)

            knob1_center = (strip_rect.centerx, strip_rect.y + 70)
            knob2_center = (strip_rect.centerx, strip_rect.y + 170)
            track_rect = pygame.Rect(strip_rect.x + 14, strip_rect.bottom - 82, strip_rect.width - 28, 54)

            self._draw_knob(surface, knob1_center, knob_radius, self.knob_values[knob_key(1, idx)], f"T{idx + 1}")
            self._draw_knob(surface, knob2_center, knob_radius, self.knob_values[knob_key(2, idx)], f"S{idx + 1}")

            track_name = track_key(idx)
            track_color = launch_control_led_color(self.track_leds[track_name])
            pygame.draw.rect(surface, track_color, track_rect, border_radius=16)
            border_color = TEXT_PRIMARY if self.active_button == ("track", track_name) else (66, 77, 96)
            pygame.draw.rect(surface, border_color, track_rect, 3, border_radius=16)
            _draw_text(surface, app.fonts["body"], f"{idx + 1}", track_rect.center, TEXT_PRIMARY, anchor="center")

            self.hitboxes.append(("knob", knob_key(1, idx), pygame.Rect(knob1_center[0] - knob_radius, knob1_center[1] - knob_radius, knob_radius * 2, knob_radius * 2)))
            self.hitboxes.append(("knob", knob_key(2, idx), pygame.Rect(knob2_center[0] - knob_radius, knob2_center[1] - knob_radius, knob_radius * 2, knob_radius * 2)))
            self.hitboxes.append(("track", track_name, track_rect))

        arrows_rect = pygame.Rect(inner.right - 296, rect.bottom - 108, 272, 84)
        arrow_layout = {
            "up": pygame.Rect(arrows_rect.x + 92, arrows_rect.y, 88, 36),
            "left": pygame.Rect(arrows_rect.x, arrows_rect.y + 44, 88, 36),
            "down": pygame.Rect(arrows_rect.x + 92, arrows_rect.y + 44, 88, 36),
            "right": pygame.Rect(arrows_rect.x + 184, arrows_rect.y + 44, 88, 36),
        }
        for name, arrow_rect in arrow_layout.items():
            led_color = launch_control_led_color(self.arrow_leds[arrow_key(name)])
            pygame.draw.rect(surface, led_color, arrow_rect, border_radius=14)
            border_color = TEXT_PRIMARY if self.active_button == ("arrow", arrow_key(name)) else (66, 77, 96)
            pygame.draw.rect(surface, border_color, arrow_rect, 3, border_radius=14)
            _draw_text(surface, app.fonts["body"], name.upper(), arrow_rect.center, TEXT_PRIMARY, anchor="center")
            self.hitboxes.append(("arrow", arrow_key(name), arrow_rect))

        footer = pygame.Rect(inner.x, rect.bottom - 76, inner.width - 320, 56)
        if status is not None:
            _draw_info_badge(surface, footer, f"TX {int(status['short_out_messages'])}", ACCENT)
            _draw_info_badge(surface, footer.move(145, 0), f"RX {int(status['short_in_messages'])}", ACCENT_ALT)
            _draw_info_badge(surface, footer.move(290, 0), f"Heartbeat {status['heartbeat_name']}", (110, 132, 242))

    def handle_pointer_down(self, app: LaunchPiControllerApp, pos: tuple[int, int]) -> None:
        if app.effect_device is None:
            return
        hit = self._hit_test(pos)
        if hit is None:
            return
        kind, key = hit
        if kind == "knob":
            self.drag_state = DragState(key=key, start_y=pos[1], start_value=self.knob_values[key])
            return

        if kind == "track":
            note = TRACK_BUTTON_NOTES[int(key.rsplit("_", 1)[-1])]
            app.effect_device.send_note(note, 127)
            self.active_button = (kind, key)
            return

        if kind == "arrow":
            cc = ARROW_CCS[key.split("_", 1)[1]]
            app.effect_device.send_cc(cc, 127)
            self.active_button = (kind, key)

    def handle_pointer_up(self, app: LaunchPiControllerApp, pos: tuple[int, int]) -> None:
        if app.effect_device is None:
            self.drag_state = None
            self.active_button = None
            return

        if self.active_button is not None:
            kind, key = self.active_button
            if kind == "track":
                note = TRACK_BUTTON_NOTES[int(key.rsplit("_", 1)[-1])]
                app.effect_device.send_note(note, 0)
            elif kind == "arrow":
                cc = ARROW_CCS[key.split("_", 1)[1]]
                app.effect_device.send_cc(cc, 0)

        self.drag_state = None
        self.active_button = None

    def handle_pointer_motion(
        self,
        app: LaunchPiControllerApp,
        pos: tuple[int, int],
        rel: tuple[int, int],
        buttons: tuple[int, int, int],
    ) -> None:
        if self.drag_state is None or app.effect_device is None:
            return

        delta = self.drag_state.start_y - pos[1]
        scaled = self.drag_state.start_value + int(delta * 0.75)
        value = max(0, min(127, scaled))
        if value == self.knob_values[self.drag_state.key]:
            return

        self.knob_values[self.drag_state.key] = value
        _, row_str, idx_str = self.drag_state.key.split("_")
        row = int(row_str)
        idx = int(idx_str)
        cc_list = ROW1_KNOB_CCS if row == 1 else ROW2_KNOB_CCS
        app.effect_device.send_cc(cc_list[idx], value)

    def handle_midi_message(self, message: MidiShortMessage, channel: int) -> None:
        if message.channel != channel:
            return

        kind = message.kind
        if kind in (0x80, 0x90):
            velocity = 0 if kind == 0x80 else message.data2
            if kind == 0x90 and message.data2 == 0:
                velocity = 0
            if message.data1 in TRACK_BUTTON_NOTES:
                idx = TRACK_BUTTON_NOTES.index(message.data1)
                self.track_leds[track_key(idx)] = velocity
            return

        if kind != 0xB0:
            return

        if message.data1 in ARROW_CCS.values():
            for name, cc_num in ARROW_CCS.items():
                if cc_num == message.data1:
                    self.arrow_leds[arrow_key(name)] = message.data2
                    return

        if message.data1 in ROW1_KNOB_CCS:
            self.knob_values[knob_key(1, ROW1_KNOB_CCS.index(message.data1))] = message.data2
            return

        if message.data1 in ROW2_KNOB_CCS:
            self.knob_values[knob_key(2, ROW2_KNOB_CCS.index(message.data1))] = message.data2

    def _hit_test(self, pos: tuple[int, int]) -> tuple[str, str] | None:
        for kind, key, rect in reversed(self.hitboxes):
            if rect.collidepoint(pos):
                return kind, key
        return None

    def _draw_knob(
        self,
        surface: pygame.Surface,
        center: tuple[int, int],
        radius: int,
        value: int,
        label: str,
    ) -> None:
        pygame.draw.circle(surface, (15, 20, 28), center, radius + 12)
        pygame.draw.circle(surface, (52, 63, 78), center, radius + 6, 6)
        pygame.draw.circle(surface, (94, 108, 124), center, radius)

        start_angle = math.radians(225)
        sweep = math.radians(270)
        value_ratio = value / 127.0
        end_angle = start_angle - sweep * value_ratio
        pygame.draw.arc(
            surface,
            ACCENT,
            pygame.Rect(center[0] - radius - 8, center[1] - radius - 8, (radius + 8) * 2, (radius + 8) * 2),
            start_angle,
            end_angle,
            6,
        )
        pointer_length = radius - 8
        pointer_angle = start_angle - sweep * value_ratio
        pointer_pos = (
            int(center[0] + math.cos(pointer_angle) * pointer_length),
            int(center[1] - math.sin(pointer_angle) * pointer_length),
        )
        pygame.draw.line(surface, (12, 16, 24), center, pointer_pos, 4)
        _draw_text(surface, _get_font("DejaVu Sans", 18, True), label, (center[0], center[1] - radius - 22), TEXT_MUTED, anchor="center")
        _draw_text(surface, _get_font("DejaVu Sans Mono", 18, True), f"{value:03d}", (center[0], center[1]), TEXT_PRIMARY, anchor="center")


class PlaceholderTab(BaseTab):
    def __init__(self, title: str, subtitle: str) -> None:
        self.title = title
        self.subtitle = subtitle

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        _draw_panel(surface, rect, self.title, self.subtitle)
        inner = rect.inflate(-24, -24)
        _draw_center_notice(
            surface,
            inner,
            "Spec still open",
            "This tab is intentionally a placeholder until the MIDI contract is defined.",
        )


class SettingsTab(BaseTab):
    title = "Settings"

    def __init__(self) -> None:
        self.field_hitboxes: list[tuple[str, pygame.Rect]] = []
        self.keypad_hitboxes: list[tuple[str, pygame.Rect]] = []
        self.action_hitboxes: list[tuple[str, pygame.Rect]] = []
        self.selected_field_id: str | None = None
        self.edit_buffer = ""
        self.status_text = "Tap a field to edit, then APPLY."
        self.status_color = TEXT_MUTED

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        _draw_panel(surface, rect, "Settings", "Network endpoints and preview settings for v1")
        inner = rect.inflate(-24, -24)
        left = pygame.Rect(inner.x, inner.y + 40, max(560, inner.width // 2), inner.height - 56)
        right = pygame.Rect(left.right + 20, inner.y + 40, inner.right - left.right - 20, inner.height - 56)

        fields = self._fields(app)
        self.field_hitboxes = []
        row_h = 44
        for idx, field in enumerate(fields):
            row = pygame.Rect(left.x, left.y + idx * (row_h + 8), left.width, row_h)
            bg = PANEL_ALT if field["editable"] else (18, 24, 34)
            pygame.draw.rect(surface, bg, row, border_radius=12)
            border_color = ACCENT if self.selected_field_id == field["id"] else (53, 65, 82)
            pygame.draw.rect(surface, border_color, row, 2, border_radius=12)
            _draw_text(surface, app.fonts["body"], field["label"], (row.x + 16, row.centery), TEXT_MUTED)
            _draw_text(surface, app.fonts["mono"], field["value"], (row.right - 16, row.centery), TEXT_PRIMARY, anchor="right")
            self.field_hitboxes.append((field["id"], row))

        buttons_top = pygame.Rect(right.x, right.y, right.width, 116)
        action_defs = [
            ("toggle_heartbeat", f"Heartbeat {'ON' if app.config.effect_device.send_heartbeat else 'OFF'}", ACCENT_ALT),
            ("toggle_sync", f"Preview Sync {'ON' if app.config.preview.use_sync else 'OFF'}", ACCENT),
            ("toggle_fullscreen", f"Fullscreen {'ON' if app.config.display.fullscreen else 'OFF'}", (110, 132, 242)),
            ("restart_runtime", "Restart I/O", SUCCESS),
        ]
        self.action_hitboxes = []
        for idx, (action_id, label, color) in enumerate(action_defs):
            bx = buttons_top.x + (idx % 2) * ((buttons_top.width - 12) // 2 + 12)
            by = buttons_top.y + (idx // 2) * 56
            button_rect = pygame.Rect(bx, by, (buttons_top.width - 12) // 2, 44)
            pygame.draw.rect(surface, color, button_rect, border_radius=14)
            pygame.draw.rect(surface, TEXT_PRIMARY, button_rect, 2, border_radius=14)
            _draw_text(surface, app.fonts["body"], label, button_rect.center, (15, 18, 25), anchor="center")
            self.action_hitboxes.append((action_id, button_rect))

        status_rect = pygame.Rect(right.x, buttons_top.bottom + 18, right.width, 48)
        pygame.draw.rect(surface, PANEL_ALT, status_rect, border_radius=12)
        pygame.draw.rect(surface, self.status_color, status_rect, 2, border_radius=12)
        _draw_text(surface, app.fonts["body"], self.status_text, status_rect.center, TEXT_PRIMARY, anchor="center")

        editor_rect = pygame.Rect(right.x, status_rect.bottom + 18, right.width, 54)
        pygame.draw.rect(surface, (11, 14, 22), editor_rect, border_radius=12)
        pygame.draw.rect(surface, ACCENT if self.selected_field_id else (53, 65, 82), editor_rect, 2, border_radius=12)
        editor_text = self.edit_buffer if self.selected_field_id else "Select an editable field"
        _draw_text(surface, app.fonts["mono"], editor_text, (editor_rect.x + 16, editor_rect.centery), TEXT_PRIMARY)

        keypad_rect = pygame.Rect(right.x, editor_rect.bottom + 16, right.width, right.bottom - editor_rect.bottom - 16)
        self._draw_keypad(app, surface, keypad_rect)

    def handle_pointer_down(self, app: LaunchPiControllerApp, pos: tuple[int, int]) -> None:
        for field_id, rect in self.field_hitboxes:
            if not rect.collidepoint(pos):
                continue
            editable = next((f for f in self._fields(app) if f["id"] == field_id), None)
            if editable is None or not editable["editable"]:
                self.status_text = "This field is display-only in v1."
                self.status_color = TEXT_MUTED
                return
            self.selected_field_id = field_id
            self.edit_buffer = editable["value"]
            self.status_text = f"Editing {editable['label']}"
            self.status_color = ACCENT
            return

        for key, rect in self.keypad_hitboxes:
            if rect.collidepoint(pos):
                self._handle_keypress(app, key)
                return

        for action_id, rect in self.action_hitboxes:
            if rect.collidepoint(pos):
                self._handle_action(app, action_id)
                return

    def _draw_keypad(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        keys = [
            "7", "8", "9", "DEL",
            "4", "5", "6", "CLR",
            "1", "2", "3", ".",
            "0", "APPLY", "SAVE", "RELOAD",
        ]
        self.keypad_hitboxes = []
        cell_w = (rect.width - 18) // 4
        cell_h = (rect.height - 18) // 4
        for idx, key in enumerate(keys):
            row = idx // 4
            col = idx % 4
            button_rect = pygame.Rect(rect.x + col * (cell_w + 6), rect.y + row * (cell_h + 6), cell_w, cell_h)
            color = (27, 36, 49)
            if key in {"APPLY", "SAVE"}:
                color = ACCENT
            elif key == "RELOAD":
                color = ACCENT_ALT
            pygame.draw.rect(surface, color, button_rect, border_radius=16)
            pygame.draw.rect(surface, TEXT_PRIMARY, button_rect, 2, border_radius=16)
            text_color = (15, 18, 25) if key in {"APPLY", "SAVE"} else TEXT_PRIMARY
            _draw_text(surface, app.fonts["body"], key, button_rect.center, text_color, anchor="center")
            self.keypad_hitboxes.append((key, button_rect))

    def _fields(self, app: LaunchPiControllerApp) -> list[dict[str, Any]]:
        cfg = app.config
        return [
            {"id": "effect_target_host", "label": "Effect target host", "value": cfg.effect_device.target_host, "editable": True},
            {"id": "effect_port", "label": "Effect UDP port", "value": str(cfg.effect_device.port), "editable": True},
            {"id": "effect_bind_host", "label": "Effect bind host", "value": cfg.effect_device.bind_host, "editable": True},
            {"id": "heartbeat_target_host", "label": "Heartbeat host", "value": cfg.effect_device.heartbeat_target_host, "editable": True},
            {"id": "heartbeat_port", "label": "Heartbeat port", "value": str(cfg.effect_device.heartbeat_port), "editable": True},
            {"id": "heartbeat_name", "label": "Heartbeat name", "value": cfg.effect_device.heartbeat_name, "editable": False},
            {"id": "preview_bind_host", "label": "Preview bind host", "value": cfg.preview.bind_host, "editable": True},
            {"id": "preview_port", "label": "Preview Art-Net port", "value": str(cfg.preview.port), "editable": True},
            {"id": "preview_net", "label": "Preview net", "value": str(cfg.preview.net), "editable": True},
            {"id": "preview_cols", "label": "Preview cols", "value": str(cfg.preview.cols), "editable": True},
            {"id": "preview_rows", "label": "Preview rows", "value": str(cfg.preview.rows), "editable": True},
        ]

    def _handle_keypress(self, app: LaunchPiControllerApp, key: str) -> None:
        if key == "SAVE":
            try:
                app.save_config()
                self.status_text = f"Saved {app.config_path.name}"
                self.status_color = SUCCESS
            except OSError as ex:
                self.status_text = str(ex)
                self.status_color = WARNING
            return

        if key == "RELOAD":
            try:
                app.reload_config_from_disk()
                self.selected_field_id = None
                self.edit_buffer = ""
                self.status_text = "Reloaded config from disk"
                self.status_color = SUCCESS
            except OSError as ex:
                self.status_text = str(ex)
                self.status_color = WARNING
            return

        if self.selected_field_id is None:
            self.status_text = "Select a field first"
            self.status_color = TEXT_MUTED
            return

        if key == "DEL":
            self.edit_buffer = self.edit_buffer[:-1]
            return
        if key == "CLR":
            self.edit_buffer = ""
            return
        if key == "APPLY":
            self._apply_buffer(app)
            return
        self.edit_buffer += key

    def _apply_buffer(self, app: LaunchPiControllerApp) -> None:
        assert self.selected_field_id is not None
        value = self.edit_buffer.strip()
        try:
            if self.selected_field_id in {"effect_target_host", "effect_bind_host", "heartbeat_target_host", "preview_bind_host"}:
                _validate_ip(value)
                self._assign_string_field(app, self.selected_field_id, value)
            elif self.selected_field_id in {"effect_port", "heartbeat_port", "preview_port"}:
                parsed = _validate_int(value, 1, 65535)
                self._assign_int_field(app, self.selected_field_id, parsed)
            elif self.selected_field_id == "preview_net":
                parsed = _validate_int(value, 0, 255)
                app.config.preview.net = parsed
            elif self.selected_field_id in {"preview_cols", "preview_rows"}:
                parsed = _validate_int(value, 1, 256)
                self._assign_int_field(app, self.selected_field_id, parsed)
            else:
                raise ValueError("This field cannot be edited in v1")

            app.restart_services()
            self.status_text = "Applied runtime config"
            self.status_color = SUCCESS
        except ValueError as ex:
            self.status_text = str(ex)
            self.status_color = WARNING

    def _assign_string_field(self, app: LaunchPiControllerApp, field_id: str, value: str) -> None:
        if field_id == "effect_target_host":
            app.config.effect_device.target_host = value
        elif field_id == "effect_bind_host":
            app.config.effect_device.bind_host = value
        elif field_id == "heartbeat_target_host":
            app.config.effect_device.heartbeat_target_host = value
        elif field_id == "preview_bind_host":
            app.config.preview.bind_host = value

    def _assign_int_field(self, app: LaunchPiControllerApp, field_id: str, value: int) -> None:
        if field_id == "effect_port":
            app.config.effect_device.port = value
        elif field_id == "heartbeat_port":
            app.config.effect_device.heartbeat_port = value
        elif field_id == "preview_port":
            app.config.preview.port = value
        elif field_id == "preview_cols":
            app.config.preview.cols = value
        elif field_id == "preview_rows":
            app.config.preview.rows = value

    def _handle_action(self, app: LaunchPiControllerApp, action_id: str) -> None:
        if action_id == "toggle_heartbeat":
            app.config.effect_device.send_heartbeat = not app.config.effect_device.send_heartbeat
            app.restart_services()
            self.status_text = "Heartbeat setting updated"
            self.status_color = SUCCESS
            return
        if action_id == "toggle_sync":
            app.config.preview.use_sync = not app.config.preview.use_sync
            self.status_text = "Preview sync flag updated"
            self.status_color = SUCCESS
            return
        if action_id == "toggle_fullscreen":
            app.config.display.fullscreen = not app.config.display.fullscreen
            app.apply_display_mode()
            self.status_text = "Display mode updated"
            self.status_color = SUCCESS
            return
        if action_id == "restart_runtime":
            app.restart_services()
            self.status_text = "Runtime services restarted"
            self.status_color = SUCCESS


def _validate_ip(value: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as ex:
        raise ValueError("Use an IPv4/IPv6 address in v1 settings") from ex


def _validate_int(value: str, minimum: int, maximum: int) -> int:
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"Value must be in [{minimum}, {maximum}]")
    return parsed


def _draw_panel(surface: pygame.Surface, rect: pygame.Rect, title: str, subtitle: str) -> None:
    pygame.draw.rect(surface, PANEL_BACKGROUND, rect, border_radius=28)
    pygame.draw.rect(surface, (52, 65, 83), rect, 2, border_radius=28)
    _draw_text(surface, _get_font("DejaVu Sans", 30, True), title, (rect.x + 28, rect.y + 26), TEXT_PRIMARY)
    _draw_text(surface, _get_font("DejaVu Sans", 18, False), subtitle, (rect.x + 30, rect.y + 62), TEXT_MUTED)


def _draw_center_notice(surface: pygame.Surface, rect: pygame.Rect, title: str, subtitle: str) -> None:
    _draw_text(surface, _get_font("DejaVu Sans", 30, True), title, (rect.centerx, rect.centery - 16), TEXT_PRIMARY, anchor="center")
    _draw_text(surface, _get_font("DejaVu Sans", 20, False), subtitle, (rect.centerx, rect.centery + 16), TEXT_MUTED, anchor="center")


def _draw_text(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    pos: tuple[int, int],
    color: tuple[int, int, int],
    anchor: str = "left",
) -> None:
    rendered = font.render(text, True, color)
    rect = rendered.get_rect()
    if anchor == "center":
        rect.center = pos
    elif anchor == "right":
        rect.midright = pos
    else:
        rect.midleft = pos
    surface.blit(rendered, rect)


def _draw_info_badge(surface: pygame.Surface, rect: pygame.Rect, text: str, color: tuple[int, int, int]) -> None:
    badge = pygame.Rect(rect.x, rect.y, min(rect.width, max(132, 26 + len(text) * 11)), rect.height)
    pygame.draw.rect(surface, color, badge, border_radius=17)
    pygame.draw.rect(surface, (240, 244, 247), badge, 2, border_radius=17)
    _draw_text(surface, _get_font("DejaVu Sans", 17, True), text, badge.center, (12, 16, 24), anchor="center")


def _get_font(name: str, size: int, bold: bool) -> pygame.font.Font:
    key = (name, size, bold)
    font = _FONT_CACHE.get(key)
    if font is None:
        font = pygame.font.SysFont(name, size, bold=bold)
        _FONT_CACHE[key] = font
    return font
