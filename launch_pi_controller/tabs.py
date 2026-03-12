from __future__ import annotations

from dataclasses import dataclass
import ipaddress
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


PANEL_BACKGROUND = (48, 49, 53)
PANEL_ALT = (61, 62, 68)
STATUS_BACKGROUND = (34, 35, 40)
STATUS_ALT = (52, 53, 58)
TEXT_PRIMARY = (234, 229, 221)
TEXT_MUTED = (171, 164, 152)
TEXT_DARK = (28, 24, 22)
ACCENT = (235, 134, 40)
ACCENT_ALT = (190, 92, 24)
OUTLINE = (108, 99, 89)
WARNING = (202, 96, 70)
SUCCESS = (247, 171, 92)
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

    def __init__(self) -> None:
        self._small_surface: pygame.Surface | None = None
        self._scaled_surface: pygame.Surface | None = None
        self._cached_generation = -1
        self._cached_size: tuple[int, int] = (0, 0)

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        main_rect, status_rect = _draw_tab_shell(surface, rect, "Preview", "Signal / Art-Net", status_width=312)
        preview_rect = main_rect.inflate(-2, -2)

        if app.preview_service is None:
            _draw_status_box_message(surface, status_rect, "Offline", app.preview_error or "Preview service is not running")
            _draw_center_notice(surface, preview_rect, "Preview offline", "Bind error or service not started")
            return

        stats = app.preview_service.get_stats()
        generation = int(stats["visible_generation"])
        if (
            self._small_surface is None
            or self._small_surface.get_size() != (int(stats["cols"]), int(stats["rows"]))
        ):
            self._small_surface = pygame.Surface((int(stats["cols"]), int(stats["rows"])))
            self._cached_generation = -1
        if self._scaled_surface is None or self._cached_size != preview_rect.size:
            self._scaled_surface = pygame.Surface(preview_rect.size)
            self._cached_size = preview_rect.size
            self._cached_generation = -1

        if generation != self._cached_generation:
            frame = app.preview_service.get_frame_copy()
            arr = frame.transpose((1, 0, 2))
            pygame.surfarray.blit_array(self._small_surface, arr)
            pygame.transform.scale(self._small_surface, preview_rect.size, self._scaled_surface)
            self._cached_generation = generation

        surface.blit(self._scaled_surface, preview_rect.topleft)
        pygame.draw.rect(surface, (83, 79, 74), preview_rect, 1, border_radius=12)
        fps_rect = pygame.Rect(preview_rect.x + 10, preview_rect.y + 10, 96, 28)
        pygame.draw.rect(surface, (24, 24, 27), fps_rect, border_radius=10)
        pygame.draw.rect(surface, OUTLINE, fps_rect, 1, border_radius=10)
        _draw_text(surface, _get_font("DejaVu Sans Mono", 15, True), f"{app.render_fps:04.1f} fps", fps_rect.center, TEXT_PRIMARY, anchor="center")

        if not stats["has_signal"]:
            overlay = pygame.Surface(preview_rect.size, pygame.SRCALPHA)
            overlay.fill((18, 17, 18, 170))
            surface.blit(overlay, preview_rect.topleft)
            _draw_center_notice(surface, preview_rect, "No Art-Net", "Waiting for frames")

        _draw_status_lines(
            surface,
            status_rect,
            [
                ("Matrix", f"{int(stats['cols'])} x {int(stats['rows'])}"),
                ("DMX packets", str(int(stats["dmx_packets"]))),
                ("Sync packets", str(int(stats["sync_packets"]))),
                ("Render fps", f"{app.render_fps:.1f}"),
                ("Use sync", "Yes" if bool(stats["use_sync"]) else "No"),
                ("Last DMX", _format_age(float(stats["last_dmx_age_s"]))),
                ("Last Sync", _format_age(float(stats["last_sync_age_s"]))),
            ],
        )


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
        self.slider_rects: dict[str, pygame.Rect] = {}
        self.drag_state: DragState | None = None
        self.active_button: tuple[str, str] | None = None

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        main_rect, status_rect = _draw_tab_shell(surface, rect, "Effect", "Controller Status", status_width=336)
        self.hitboxes = []

        if app.effect_device is None:
            _draw_status_box_message(surface, status_rect, "Offline", app.effect_error or "Effect MIDI device not started")
            _draw_center_notice(surface, main_rect, "Device offline", "Check bind/target settings")
            return

        status = app.effect_device.get_status()
        controls_rect = main_rect.inflate(-4, -4)
        gap = 6
        strip_width = max(112, (controls_rect.width - gap * 9) // 8)
        strip_height = controls_rect.height
        slider_gap = 8
        slider_w = max(34, min(42, (strip_width - 28) // 2))
        slider_h = strip_height - 118
        slider_top = controls_rect.y + 34

        self.slider_rects = {}
        for idx in range(8):
            strip_x = controls_rect.x + gap + idx * (strip_width + gap)
            strip_rect = pygame.Rect(strip_x, controls_rect.y, strip_width, strip_height)
            pygame.draw.rect(surface, PANEL_ALT, strip_rect, border_radius=18)
            pygame.draw.rect(surface, OUTLINE, strip_rect, 1, border_radius=18)

            slider_x = strip_rect.x + (strip_rect.width - (slider_w * 2 + slider_gap)) // 2
            slider1_rect = pygame.Rect(slider_x, slider_top, slider_w, slider_h)
            slider2_rect = pygame.Rect(slider_x + slider_w + slider_gap, slider_top, slider_w, slider_h)

            button_w = min(72, strip_rect.width - 22)
            button_h = int(button_w * 0.75)
            track_rect = pygame.Rect(strip_rect.centerx - button_w // 2, strip_rect.bottom - button_h - 10, button_w, button_h)

            _draw_text(surface, _get_font("DejaVu Sans", 15, True), f"CH {idx + 1}", (strip_rect.centerx, strip_rect.y + 18), TEXT_MUTED, anchor="center")
            self._draw_slider(surface, slider1_rect, self.knob_values[knob_key(1, idx)], f"T{idx + 1}")
            self._draw_slider(surface, slider2_rect, self.knob_values[knob_key(2, idx)], f"S{idx + 1}")

            track_name = track_key(idx)
            track_color = launch_control_led_color(self.track_leds[track_name])
            pygame.draw.rect(surface, track_color, track_rect, border_radius=12)
            border = TEXT_PRIMARY if self.active_button == ("track", track_name) else OUTLINE
            pygame.draw.rect(surface, border, track_rect, 2, border_radius=12)
            _draw_text(surface, _get_font("DejaVu Sans", 16, True), f"{idx + 1}", track_rect.center, TEXT_DARK, anchor="center")

            self.slider_rects[knob_key(1, idx)] = slider1_rect
            self.slider_rects[knob_key(2, idx)] = slider2_rect
            self.hitboxes.append(("slider", knob_key(1, idx), slider1_rect))
            self.hitboxes.append(("slider", knob_key(2, idx), slider2_rect))
            self.hitboxes.append(("track", track_name, track_rect))

        arrow_area = _draw_status_lines(
            surface,
            status_rect,
            [
                ("Target", str(status["target_host"])),
                ("Port", str(status["port"])),
                ("Feedback", _format_feedback_age(float(status["last_rx_age_s"]))),
                ("TX", str(int(status["short_out_messages"]))),
                ("RX", str(int(status["short_in_messages"]))),
                ("Heartbeat", str(status["heartbeat_name"])),
            ],
            reserve_bottom=168,
        )

        arrow_gap = 10
        arrow_w = min(120, (arrow_area.width - arrow_gap) // 2)
        arrow_h = int(arrow_w * 0.75)
        arrow_total_w = arrow_w * 2 + arrow_gap
        arrow_total_h = arrow_h * 2 + arrow_gap
        base_x = arrow_area.x + max(0, (arrow_area.width - arrow_total_w) // 2)
        base_y = arrow_area.y + max(0, (arrow_area.height - arrow_total_h) // 2)
        arrow_layout = {
            "up": pygame.Rect(base_x, base_y, arrow_w, arrow_h),
            "right": pygame.Rect(base_x + arrow_w + arrow_gap, base_y, arrow_w, arrow_h),
            "left": pygame.Rect(base_x, base_y + arrow_h + arrow_gap, arrow_w, arrow_h),
            "down": pygame.Rect(base_x + arrow_w + arrow_gap, base_y + arrow_h + arrow_gap, arrow_w, arrow_h),
        }

        for name, arrow_rect in arrow_layout.items():
            led_color = launch_control_led_color(self.arrow_leds[arrow_key(name)])
            pygame.draw.rect(surface, led_color, arrow_rect, border_radius=12)
            border = TEXT_PRIMARY if self.active_button == ("arrow", arrow_key(name)) else OUTLINE
            pygame.draw.rect(surface, border, arrow_rect, 2, border_radius=12)
            _draw_text(surface, _get_font("DejaVu Sans", 15, True), name.upper(), arrow_rect.center, TEXT_DARK, anchor="center")
            self.hitboxes.append(("arrow", arrow_key(name), arrow_rect))

    def handle_pointer_down(self, app: LaunchPiControllerApp, pos: tuple[int, int]) -> None:
        if app.effect_device is None:
            return
        hit = self._hit_test(pos)
        if hit is None:
            return
        kind, key = hit
        if kind == "slider":
            self.drag_state = DragState(key=key, start_y=pos[1], start_value=self.knob_values[key])
            self._apply_slider_position(app, key, pos[1])
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

        self._apply_slider_position(app, self.drag_state.key, pos[1])

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

    def _apply_slider_position(self, app: LaunchPiControllerApp, key: str, pos_y: int) -> None:
        slider_rect = self.slider_rects.get(key)
        if slider_rect is None:
            return
        clamped_y = max(slider_rect.top, min(slider_rect.bottom, pos_y))
        ratio = 1.0 - ((clamped_y - slider_rect.top) / max(1, slider_rect.height))
        value = max(0, min(127, int(round(ratio * 127))))
        if value == self.knob_values[key]:
            return
        self.knob_values[key] = value
        _, row_str, idx_str = key.split("_")
        row = int(row_str)
        idx = int(idx_str)
        cc_list = ROW1_KNOB_CCS if row == 1 else ROW2_KNOB_CCS
        app.effect_device.send_cc(cc_list[idx], value)

    def _draw_slider(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        value: int,
        label: str,
    ) -> None:
        pygame.draw.rect(surface, STATUS_BACKGROUND, rect, border_radius=12)
        pygame.draw.rect(surface, OUTLINE, rect, 2, border_radius=12)
        track_rect = pygame.Rect(rect.centerx - 6, rect.y + 18, 12, rect.height - 36)
        pygame.draw.rect(surface, (80, 82, 88), track_rect, border_radius=6)
        fill_height = max(8, int((value / 127.0) * track_rect.height))
        fill_rect = pygame.Rect(track_rect.x, track_rect.bottom - fill_height, track_rect.width, fill_height)
        pygame.draw.rect(surface, ACCENT, fill_rect, border_radius=6)
        thumb_y = track_rect.bottom - int((value / 127.0) * track_rect.height)
        thumb_top = max(rect.y + 18, min(rect.bottom - 34, thumb_y - 8))
        thumb_rect = pygame.Rect(rect.x + 6, thumb_top, rect.width - 12, 16)
        pygame.draw.rect(surface, TEXT_PRIMARY, thumb_rect, border_radius=8)
        pygame.draw.rect(surface, (82, 76, 70), thumb_rect, 2, border_radius=8)
        _draw_text(surface, _get_font("DejaVu Sans", 14, True), label, (rect.centerx, rect.y + 10), TEXT_MUTED, anchor="center")
        _draw_text(surface, _get_font("DejaVu Sans Mono", 14, True), f"{value:03d}", (rect.centerx, rect.bottom - 10), TEXT_PRIMARY, anchor="center")


class PlaceholderTab(BaseTab):
    def __init__(self, title: str, subtitle: str) -> None:
        self.title = title
        self.subtitle = subtitle

    def draw(self, app: LaunchPiControllerApp, surface: pygame.Surface, rect: pygame.Rect) -> None:
        main_rect, status_rect = _draw_tab_shell(surface, rect, self.title, "Status", status_width=300)
        _draw_center_notice(surface, main_rect, "Waiting for spec", self.subtitle)
        _draw_status_lines(
            surface,
            status_rect,
            [
                ("State", "Blocked"),
                ("Reason", "Contract missing"),
                ("Action", "Define MIDI I/O"),
            ],
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
        main_rect, status_rect = _draw_tab_shell(surface, rect, "Settings", "Runtime / Save", status_width=520)
        self._draw_fields(surface, app, main_rect)
        self._draw_actions(surface, app, status_rect)

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

    def _draw_fields(self, surface: pygame.Surface, app: LaunchPiControllerApp, rect: pygame.Rect) -> None:
        self.field_hitboxes = []
        fields = self._fields(app)
        columns = 2
        gap = 8
        row_h = 38
        col_w = (rect.width - gap) // columns
        for idx, field in enumerate(fields):
            col = idx // 6
            row = idx % 6
            field_rect = pygame.Rect(rect.x + col * (col_w + gap), rect.y + row * (row_h + gap), col_w, row_h)
            bg = PANEL_ALT if field["editable"] else STATUS_ALT
            pygame.draw.rect(surface, bg, field_rect, border_radius=10)
            border = ACCENT if self.selected_field_id == field["id"] else OUTLINE
            pygame.draw.rect(surface, border, field_rect, 2, border_radius=10)
            _draw_text(surface, _get_font("DejaVu Sans", 15, True), field["label"], (field_rect.x + 12, field_rect.centery - 8), TEXT_MUTED)
            _draw_text(surface, _get_font("DejaVu Sans Mono", 15, False), field["value"], (field_rect.x + 12, field_rect.centery + 9), TEXT_PRIMARY)
            self.field_hitboxes.append((field["id"], field_rect))

    def _draw_actions(self, surface: pygame.Surface, app: LaunchPiControllerApp, rect: pygame.Rect) -> None:
        self.action_hitboxes = []
        action_defs = [
            ("toggle_heartbeat", f"Heartbeat {'ON' if app.config.effect_device.send_heartbeat else 'OFF'}", ACCENT_ALT),
            ("toggle_sync", f"Sync {'ON' if app.config.preview.use_sync else 'OFF'}", ACCENT),
            ("toggle_fullscreen", f"Fullscreen {'ON' if app.config.display.fullscreen else 'OFF'}", SUCCESS),
            ("restart_runtime", "Restart I/O", ACCENT),
        ]
        button_w = (rect.width - 12) // 2
        for idx, (action_id, label, color) in enumerate(action_defs):
            bx = rect.x + (idx % 2) * (button_w + 12)
            by = rect.y + (idx // 2) * 48
            button_rect = pygame.Rect(bx, by, button_w, 40)
            pygame.draw.rect(surface, color, button_rect, border_radius=12)
            pygame.draw.rect(surface, TEXT_PRIMARY, button_rect, 2, border_radius=12)
            _draw_text(surface, _get_font("DejaVu Sans", 15, True), label, button_rect.center, TEXT_DARK, anchor="center")
            self.action_hitboxes.append((action_id, button_rect))

        status_rect = pygame.Rect(rect.x, rect.y + 98, rect.width, 42)
        pygame.draw.rect(surface, STATUS_ALT, status_rect, border_radius=10)
        pygame.draw.rect(surface, self.status_color, status_rect, 2, border_radius=10)
        _draw_text(surface, _get_font("DejaVu Sans", 15, False), self.status_text, status_rect.center, TEXT_PRIMARY, anchor="center")

        editor_rect = pygame.Rect(rect.x, status_rect.bottom + 8, rect.width, 42)
        pygame.draw.rect(surface, STATUS_BACKGROUND, editor_rect, border_radius=10)
        pygame.draw.rect(surface, ACCENT if self.selected_field_id else OUTLINE, editor_rect, 2, border_radius=10)
        editor_text = self.edit_buffer if self.selected_field_id else "Select an editable field"
        _draw_text(surface, _get_font("DejaVu Sans Mono", 15, False), editor_text, (editor_rect.x + 12, editor_rect.centery), TEXT_PRIMARY)

        keypad_rect = pygame.Rect(rect.x, editor_rect.bottom + 8, rect.width, rect.bottom - editor_rect.bottom - 8)
        self._draw_keypad(surface, keypad_rect)

    def _draw_keypad(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        keys = [
            "7", "8", "9", "DEL",
            "4", "5", "6", "CLR",
            "1", "2", "3", ".",
            "0", "APPLY", "SAVE", "RELOAD",
        ]
        self.keypad_hitboxes = []
        gap = 6
        cell_w = (rect.width - gap * 3) // 4
        cell_h = (rect.height - gap * 3) // 4
        for idx, key in enumerate(keys):
            row = idx // 4
            col = idx % 4
            button_rect = pygame.Rect(rect.x + col * (cell_w + gap), rect.y + row * (cell_h + gap), cell_w, cell_h)
            color = PANEL_ALT
            text_color = TEXT_PRIMARY
            if key in {"APPLY", "SAVE"}:
                color = ACCENT
                text_color = TEXT_DARK
            elif key == "RELOAD":
                color = ACCENT_ALT
                text_color = TEXT_DARK
            pygame.draw.rect(surface, color, button_rect, border_radius=12)
            pygame.draw.rect(surface, OUTLINE if text_color == TEXT_PRIMARY else TEXT_PRIMARY, button_rect, 2, border_radius=12)
            _draw_text(surface, _get_font("DejaVu Sans", 15, True), key, button_rect.center, text_color, anchor="center")
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
                self._assign_int_field(app, self.selected_field_id, _validate_int(value, 1, 65535))
            elif self.selected_field_id == "preview_net":
                app.config.preview.net = _validate_int(value, 0, 255)
            elif self.selected_field_id in {"preview_cols", "preview_rows"}:
                self._assign_int_field(app, self.selected_field_id, _validate_int(value, 1, 256))
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


def _draw_tab_shell(
    surface: pygame.Surface,
    rect: pygame.Rect,
    title: str,
    status_title: str,
    status_width: int,
) -> tuple[pygame.Rect, pygame.Rect]:
    pygame.draw.rect(surface, PANEL_BACKGROUND, rect, border_radius=18)
    pygame.draw.rect(surface, OUTLINE, rect, 2, border_radius=18)

    inner = rect.inflate(-8, -8)
    status_rect = pygame.Rect(inner.right - status_width, inner.y, status_width, inner.height)
    main_rect = pygame.Rect(inner.x, inner.y, status_rect.x - inner.x - 8, inner.height)

    pygame.draw.rect(surface, STATUS_BACKGROUND, status_rect, border_radius=14)
    pygame.draw.rect(surface, OUTLINE, status_rect, 2, border_radius=14)
    return main_rect, pygame.Rect(status_rect.x + 8, status_rect.y + 8, status_rect.width - 16, status_rect.height - 16)


def _draw_status_lines(
    surface: pygame.Surface,
    rect: pygame.Rect,
    lines: list[tuple[str, str]],
    reserve_bottom: int = 0,
) -> pygame.Rect:
    y = rect.y
    row_h = 32
    usable = pygame.Rect(rect.x, rect.y, rect.width, max(0, rect.height - reserve_bottom))
    for label, value in lines:
        row = pygame.Rect(usable.x, y, usable.width, 28)
        pygame.draw.rect(surface, STATUS_ALT, row, border_radius=10)
        _draw_text(surface, _get_font("DejaVu Sans", 14, True), label, (row.x + 10, row.centery), TEXT_MUTED)
        _draw_text(surface, _get_font("DejaVu Sans Mono", 14, False), value, (row.right - 10, row.centery), TEXT_PRIMARY, anchor="right")
        y += row_h
        if y + row_h > usable.bottom:
            break
    if reserve_bottom:
        return pygame.Rect(rect.x, rect.bottom - reserve_bottom + 8, rect.width, max(0, reserve_bottom - 8))
    return pygame.Rect(rect.x, y, rect.width, max(0, rect.bottom - y))


def _draw_status_box_message(surface: pygame.Surface, rect: pygame.Rect, title: str, message: str) -> None:
    _draw_text(surface, _get_font("DejaVu Sans", 18, True), title, (rect.x + 4, rect.y + 18), TEXT_PRIMARY)
    _draw_text(surface, _get_font("DejaVu Sans", 15, False), message, (rect.x + 4, rect.y + 46), TEXT_MUTED)


def _draw_center_notice(surface: pygame.Surface, rect: pygame.Rect, title: str, subtitle: str) -> None:
    _draw_text(surface, _get_font("DejaVu Sans", 28, True), title, (rect.centerx, rect.centery - 10), TEXT_PRIMARY, anchor="center")
    _draw_text(surface, _get_font("DejaVu Sans", 18, False), subtitle, (rect.centerx, rect.centery + 18), TEXT_MUTED, anchor="center")


def _format_age(value: float) -> str:
    if value < 0:
        return "never"
    return f"{value:.1f}s"


def _format_feedback_age(value: float) -> str:
    if value < 0:
        return "No feedback"
    if value < 3:
        return f"{value:.1f}s ago"
    return "stale"


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


def _get_font(name: str, size: int, bold: bool) -> pygame.font.Font:
    key = (name, size, bold)
    font = _FONT_CACHE.get(key)
    if font is None:
        font = pygame.font.SysFont(name, size, bold=bold)
        _FONT_CACHE[key] = font
    return font
