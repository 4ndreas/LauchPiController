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
        self.clock: pygame.time.Clock | None = None

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
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._handle_pointer_down(event.pos)
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self.current_tab.handle_pointer_up(self, event.pos)
                elif event.type == pygame.MOUSEMOTION and any(event.buttons):
                    self.current_tab.handle_pointer_motion(self, event.pos, event.rel, event.buttons)
                elif event.type == pygame.FINGERDOWN:
                    self._handle_pointer_down(self._finger_to_pixels(event))
                elif event.type == pygame.FINGERUP:
                    self.current_tab.handle_pointer_up(self, self._finger_to_pixels(event))
                elif event.type == pygame.FINGERMOTION:
                    pos = self._finger_to_pixels(event)
                    rel = (
                        int(event.dx * self.screen.get_width()),
                        int(event.dy * self.screen.get_height()),
                    )
                    self.current_tab.handle_pointer_motion(self, pos, rel, (1, 0, 0))

            self._draw()
            assert self.clock is not None
            self.clock.tick(max(20, int(self.config.display.fps)))

        self.close()
        return 0

    @property
    def current_tab(self) -> Any:
        return self.tabs[self.active_tab_index]

    def apply_display_mode(self) -> None:
        flags = pygame.DOUBLEBUF
        if self.config.display.fullscreen:
            flags |= pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((self.config.display.width, self.config.display.height), flags)
        pygame.display.set_caption("Pi Launchpad Controller")
        pygame.mouse.set_visible(not self.config.display.hide_mouse)

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

    def _queue_midi_message(self, message: MidiShortMessage) -> None:
        self.midi_queue.put(message)

    def _drain_midi_queue(self) -> None:
        effect_tab = self.tabs[1]
        while True:
            try:
                message = self.midi_queue.get_nowait()
            except queue.Empty:
                break
            effect_tab.handle_midi_message(message, self.config.effect_device.channel)

    def _finger_to_pixels(self, event: pygame.event.Event) -> tuple[int, int]:
        assert self.screen is not None
        return int(event.x * self.screen.get_width()), int(event.y * self.screen.get_height())

    def _handle_pointer_down(self, pos: tuple[int, int]) -> None:
        for idx, rect in self.tab_hitboxes:
            if rect.collidepoint(pos):
                self.active_tab_index = idx
                return
        self.current_tab.handle_pointer_down(self, pos)

    def _draw(self) -> None:
        assert self.screen is not None
        self._draw_background(self.screen)

        width, height = self.screen.get_size()
        sidebar = pygame.Rect(18, 18, 160, height - 36)
        content = pygame.Rect(sidebar.right + 18, 18, width - sidebar.width - 54, height - 36)

        pygame.draw.rect(self.screen, (15, 20, 28), sidebar, border_radius=26)
        pygame.draw.rect(self.screen, (58, 71, 90), sidebar, 2, border_radius=26)
        title_rect = pygame.Rect(sidebar.x + 18, sidebar.y + 16, sidebar.width - 36, 74)
        pygame.draw.rect(self.screen, PANEL_ALT, title_rect, border_radius=20)
        pygame.draw.rect(self.screen, ACCENT, title_rect, 2, border_radius=20)
        self._draw_text("Pi", (title_rect.x + 18, title_rect.y + 25), self.fonts["title"], TEXT_PRIMARY)
        self._draw_text("Controller", (title_rect.x + 18, title_rect.y + 51), self.fonts["small"], TEXT_MUTED)

        self.tab_hitboxes = []
        tab_top = title_rect.bottom + 18
        for idx, tab in enumerate(self.tabs):
            tab_rect = pygame.Rect(sidebar.x + 14, tab_top + idx * 66, sidebar.width - 28, 54)
            active = idx == self.active_tab_index
            color = ACCENT if active else (28, 36, 49)
            text_color = (15, 18, 24) if active else TEXT_PRIMARY
            pygame.draw.rect(self.screen, color, tab_rect, border_radius=18)
            pygame.draw.rect(self.screen, (236, 240, 244) if active else (61, 75, 94), tab_rect, 2, border_radius=18)
            label = tab.title if len(tab.title) <= 10 else tab.title[:10]
            self._draw_text(label, tab_rect.center, self.fonts["body"], text_color, anchor="center")
            self.tab_hitboxes.append((idx, tab_rect))

        footer = pygame.Rect(sidebar.x + 14, sidebar.bottom - 84, sidebar.width - 28, 70)
        pygame.draw.rect(self.screen, (11, 15, 21), footer, border_radius=18)
        pygame.draw.rect(self.screen, (58, 71, 90), footer, 2, border_radius=18)
        self._draw_text("F5 Restart", (footer.x + 14, footer.y + 24), self.fonts["small"], TEXT_PRIMARY)
        self._draw_text("Esc Exit", (footer.x + 14, footer.y + 46), self.fonts["small"], TEXT_MUTED)

        self.current_tab.draw(self, self.screen, content)
        pygame.display.flip()

    def _draw_background(self, surface: pygame.Surface) -> None:
        width, height = surface.get_size()
        for y in range(height):
            mix = y / max(1, height - 1)
            r = int(10 + 18 * mix)
            g = int(14 + 24 * mix)
            b = int(24 + 40 * mix)
            pygame.draw.line(surface, (r, g, b), (0, y), (width, y))
        for x in range(0, width, 120):
            alpha = 16 if (x // 120) % 2 == 0 else 8
            stripe = pygame.Surface((56, height), pygame.SRCALPHA)
            stripe.fill((120, 210, 198, alpha))
            surface.blit(stripe, (x, 0))

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
