from __future__ import annotations

from pathlib import Path
import queue
from typing import Any

import pygame

from .artnet_preview import ArtnetPreviewService
from .config import default_config_path, load_config, save_config
from .network_midi import MidiShortMessage, NetworkMidiDevice
from .tabs import (
    ACCENT,
    PANEL_ALT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    EffectTab,
    PlaceholderTab,
    PreviewTab,
    SettingsTab,
)


class LaunchPiControllerApp:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.config_path = default_config_path(base_dir)
        self.config = load_config(self.config_path)
        if not self.config_path.exists():
            save_config(self.config, self.config_path)

        self.preview_service: ArtnetPreviewService | None = None
        self.preview_error = ""
        self.effect_device: NetworkMidiDevice | None = None
        self.effect_error = ""
        self.midi_queue: queue.SimpleQueue[MidiShortMessage] = queue.SimpleQueue()

        self.tabs = [
            PreviewTab(),
            EffectTab(),
            PlaceholderTab("Color", "Reserved for the future color contract"),
            PlaceholderTab("Extras", "Reserved for the future extras contract"),
            SettingsTab(),
        ]
        self.active_tab_index = 0
        self.tab_hitboxes: list[tuple[int, pygame.Rect]] = []
        self.fonts: dict[str, pygame.font.Font] = {}
        self.screen: pygame.Surface | None = None
        self.display_surface: pygame.Surface | None = None
        self.clock: pygame.time.Clock | None = None
        self.display_rotation = 0
        self.render_fps = 0.0
        self._fps_frames = 0
        self._fps_timer_ms = 0
        self._needs_redraw = True
        self._last_ui_refresh_ms = 0
        self._last_preview_generation = -1

    def run(self) -> int:
        pygame.init()
        pygame.font.init()
        self.clock = pygame.time.Clock()
        self.fonts = {
            "title": pygame.font.SysFont("DejaVu Sans", 32, bold=True),
            "body": pygame.font.SysFont("DejaVu Sans", 20),
            "mono": pygame.font.SysFont("DejaVu Sans Mono", 18),
            "small": pygame.font.SysFont("DejaVu Sans", 16),
        }
        self.apply_display_mode()
        self.restart_services()
        self._fps_timer_ms = pygame.time.get_ticks()
        self._last_ui_refresh_ms = self._fps_timer_ms

        running = True
        while running:
            self._drain_midi_queue()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F5:
                    self.restart_services()
                    self.request_redraw()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._handle_pointer_down(self._window_to_logical(event.pos))
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self.current_tab.handle_pointer_up(self, self._window_to_logical(event.pos))
                    self.request_redraw()
                elif event.type == pygame.MOUSEMOTION and any(event.buttons):
                    self.current_tab.handle_pointer_motion(
                        self,
                        self._window_to_logical(event.pos),
                        self._window_delta_to_logical(event.rel),
                        event.buttons,
                    )
                    self.request_redraw()
                elif event.type == pygame.FINGERDOWN:
                    self._handle_pointer_down(self._finger_to_pixels(event))
                elif event.type == pygame.FINGERUP:
                    self.current_tab.handle_pointer_up(self, self._finger_to_pixels(event))
                    self.request_redraw()
                elif event.type == pygame.FINGERMOTION:
                    self.current_tab.handle_pointer_motion(
                        self,
                        self._finger_to_pixels(event),
                        self._finger_delta_to_pixels(event),
                        (1, 0, 0),
                    )
                    self.request_redraw()

            if self._should_redraw():
                self._draw()
                self._update_render_fps()
            assert self.clock is not None
            self.clock.tick(60)

        self.close()
        return 0

    @property
    def current_tab(self) -> Any:
        return self.tabs[self.active_tab_index]

    def apply_display_mode(self) -> None:
        flags = pygame.DOUBLEBUF
        if self.config.display.fullscreen:
            flags |= pygame.FULLSCREEN

        logical_size = (self.config.display.width, self.config.display.height)
        self.display_surface = pygame.display.set_mode(logical_size, flags)
        self.display_rotation = self._normalize_rotation(self.config.display.rotation)

        if self.display_rotation:
            # KMSDRM still exposes the portrait framebuffer, so render into a
            # landscape canvas and rotate it for presentation.
            self.screen = pygame.Surface(logical_size).convert()
        else:
            self.screen = self.display_surface

        pygame.display.set_caption("Pi Launchpad Controller")
        pygame.mouse.set_visible(not self.config.display.hide_mouse)
        self.request_redraw()

    def restart_services(self) -> None:
        if self.effect_device is not None:
            self.effect_device.close()
            self.effect_device = None
        if self.preview_service is not None:
            self.preview_service.close()
            self.preview_service = None

        self.effect_error = ""
        self.preview_error = ""

        try:
            self.effect_device = NetworkMidiDevice(self.config.effect_device, self._queue_midi_message)
        except OSError as ex:
            self.effect_error = f"Effect bind failed: {ex}"

        try:
            self.preview_service = ArtnetPreviewService(self.config.preview)
        except OSError as ex:
            self.preview_error = f"Preview bind failed: {ex}"
        self._last_preview_generation = -1
        self.request_redraw()

    def save_config(self) -> None:
        save_config(self.config, self.config_path)

    def reload_config_from_disk(self) -> None:
        self.config = load_config(self.config_path)
        self.apply_display_mode()
        self.restart_services()

    def close(self) -> None:
        if self.effect_device is not None:
            self.effect_device.close()
        if self.preview_service is not None:
            self.preview_service.close()
        pygame.quit()

    def request_redraw(self) -> None:
        self._needs_redraw = True

    def _queue_midi_message(self, message: MidiShortMessage) -> None:
        self.midi_queue.put(message)

    def _drain_midi_queue(self) -> None:
        effect_tab = self.tabs[1]
        saw_message = False
        while True:
            try:
                message = self.midi_queue.get_nowait()
            except queue.Empty:
                break
            effect_tab.handle_midi_message(message, self.config.effect_device.channel)
            saw_message = True
        if saw_message:
            self.request_redraw()

    def _should_redraw(self) -> bool:
        now_ms = pygame.time.get_ticks()
        if self._needs_redraw:
            return True
        if now_ms - self._last_ui_refresh_ms >= 250:
            if self.active_tab_index == 0:
                return True
            return False
        if (
            self.active_tab_index == 0
            and self.preview_service is not None
            and self.config.preview.render_mode == "image"
        ):
            stats = self.preview_service.get_stats()
            generation = int(stats["visible_generation"])
            if generation != self._last_preview_generation:
                return True
        return False

    def _normalize_rotation(self, value: int) -> int:
        normalized = int(value) % 360
        return normalized if normalized in (0, 90, 180, 270) else 0

    def _window_to_logical(self, pos: tuple[int, int]) -> tuple[int, int]:
        assert self.screen is not None
        width, height = self.screen.get_size()
        x, y = pos
        if self.display_rotation == 90:
            return self._clamp_point((width - 1 - y, x), width, height)
        if self.display_rotation == 180:
            return self._clamp_point((width - 1 - x, height - 1 - y), width, height)
        if self.display_rotation == 270:
            return self._clamp_point((y, height - 1 - x), width, height)
        return self._clamp_point((x, y), width, height)

    def _window_delta_to_logical(self, rel: tuple[int, int]) -> tuple[int, int]:
        dx, dy = rel
        if self.display_rotation == 90:
            return -dy, dx
        if self.display_rotation == 180:
            return -dx, -dy
        if self.display_rotation == 270:
            return dy, -dx
        return dx, dy

    def _clamp_point(self, pos: tuple[int, int], width: int, height: int) -> tuple[int, int]:
        x = max(0, min(width - 1, int(pos[0])))
        y = max(0, min(height - 1, int(pos[1])))
        return x, y

    def _finger_to_pixels(self, event: pygame.event.Event) -> tuple[int, int]:
        assert self.display_surface is not None
        display_w, display_h = self.display_surface.get_size()
        window_pos = (int(event.x * display_w), int(event.y * display_h))
        return self._window_to_logical(window_pos)

    def _finger_delta_to_pixels(self, event: pygame.event.Event) -> tuple[int, int]:
        assert self.display_surface is not None
        display_w, display_h = self.display_surface.get_size()
        window_rel = (int(event.dx * display_w), int(event.dy * display_h))
        return self._window_delta_to_logical(window_rel)

    def _handle_pointer_down(self, pos: tuple[int, int]) -> None:
        for idx, rect in self.tab_hitboxes:
            if rect.collidepoint(pos):
                self.active_tab_index = idx
                self.request_redraw()
                return
        self.current_tab.handle_pointer_down(self, pos)
        self.request_redraw()

    def _draw(self) -> None:
        assert self.screen is not None
        self._needs_redraw = False
        self._last_ui_refresh_ms = pygame.time.get_ticks()
        if (
            self.active_tab_index == 0
            and self.preview_service is not None
            and self.config.preview.render_mode == "image"
        ):
            stats = self.preview_service.get_stats()
            self._last_preview_generation = int(stats["visible_generation"])
        surface = self.screen
        self._draw_background(surface)

        width, height = surface.get_size()
        sidebar = pygame.Rect(8, 8, 128, height - 16)
        content = pygame.Rect(sidebar.right + 8, 8, width - sidebar.width - 24, height - 16)

        pygame.draw.rect(surface, (40, 41, 46), sidebar, border_radius=18)
        pygame.draw.rect(surface, (94, 84, 72), sidebar, 2, border_radius=18)

        self.tab_hitboxes = []
        tab_top = sidebar.y + 8
        for idx, tab in enumerate(self.tabs):
            tab_rect = pygame.Rect(sidebar.x + 22, tab_top + idx * 52, sidebar.width - 30, 46)
            active = idx == self.active_tab_index
            color = ACCENT if active else PANEL_ALT
            text_color = (26, 24, 22) if active else TEXT_PRIMARY
            border = TEXT_PRIMARY if active else (96, 88, 78)
            pygame.draw.rect(surface, color, tab_rect, border_radius=14)
            pygame.draw.rect(surface, border, tab_rect, 2, border_radius=14)
            label = tab.title if len(tab.title) <= 9 else tab.title[:9]
            self._draw_text(label, tab_rect.center, self.fonts["body"], text_color, anchor="center")
            self.tab_hitboxes.append((idx, tab_rect))

        footer = pygame.Rect(sidebar.x + 22, sidebar.bottom - 50, sidebar.width - 30, 42)
        pygame.draw.rect(surface, (28, 29, 33), footer, border_radius=12)
        pygame.draw.rect(surface, (96, 88, 78), footer, 2, border_radius=12)
        self._draw_text("F5", (footer.x + 10, footer.y + 13), self.fonts["small"], TEXT_PRIMARY)
        self._draw_text("ESC", (footer.x + 10, footer.y + 28), self.fonts["small"], TEXT_MUTED)

        self.current_tab.draw(self, surface, content)
        self._present()

    def _present(self) -> None:
        assert self.screen is not None
        assert self.display_surface is not None
        if self.display_surface is self.screen:
            pygame.display.flip()
            return

        presented = pygame.transform.rotate(self.screen, self.display_rotation)
        if presented.get_size() != self.display_surface.get_size():
            presented = pygame.transform.scale(presented, self.display_surface.get_size())
        self.display_surface.blit(presented, (0, 0))
        pygame.display.flip()

    def _draw_background(self, surface: pygame.Surface) -> None:
        surface.fill((0, 0, 0))

    def _update_render_fps(self) -> None:
        self._fps_frames += 1
        now_ms = pygame.time.get_ticks()
        elapsed = now_ms - self._fps_timer_ms
        if elapsed < 1000:
            return
        self.render_fps = self._fps_frames * 1000.0 / elapsed
        self._fps_frames = 0
        self._fps_timer_ms = now_ms

    def _draw_text(
        self,
        text: str,
        pos: tuple[int, int],
        font: pygame.font.Font,
        color: tuple[int, int, int],
        anchor: str = "left",
    ) -> None:
        rendered = font.render(text, True, color)
        rect = rendered.get_rect()
        if anchor == "center":
            rect.center = pos
        else:
            rect.midleft = pos
        assert self.screen is not None
        self.screen.blit(rendered, rect)


def main() -> int:
    return LaunchPiControllerApp(Path(__file__).resolve().parent.parent).run()
