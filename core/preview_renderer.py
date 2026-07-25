"""
# Copyright (C) 2026 Artem Honcharov(Hon4) / OWN.LAB
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
=================
core/preview_renderer.py
=========================
Перетворює grid (5×16×3, BGR) у PIL Image для показу в прев'ю-канвасі.
Логіка ідентична монолітній версії, лише координати гексагонів тепер
імпортуються з core/config.py замість глобальних змінних модуля.
"""
import cv2
import numpy as np
from PIL import Image

from core.config import ROW_CONFIG, HEX_COORDS


class PreviewRenderer:
    """Перетворює grid (5×16×3) у PIL Image для відображення."""

    WIDTH  = 860
    HEIGHT = 250
    R_HEX  = 26

    def render(self, grid: np.ndarray, is_light: bool, low_perf: bool = False) -> Image.Image:
        """low_perf=True (SETTINGS → LOW_PERF_MODE) пропускає
        _apply_glow_scanlines — найважчу частину (два gaussian blur +
        множення на скан-лінії), яка найбільше навантажує CPU саме тоді,
        коли прев'ю оновлюється з високою частотою кадрів."""
        surf = (
            np.ones((self.HEIGHT, self.WIDTH, 3), dtype=np.uint8) * 255
            if is_light else
            np.zeros((self.HEIGHT, self.WIDTH, 3), dtype=np.uint8)
        )

        for r, row_len in enumerate(ROW_CONFIG):
            is_16 = (row_len == 16)
            for c in range(row_len):
                if c >= len(HEX_COORDS[r]):
                    continue
                cx, cy    = HEX_COORDS[r][c]
                color_idx = c if is_16 else c + 1
                color     = grid[r, color_idx].tolist()
                pts       = self._hex_pts(cx, cy)
                cv2.fillPoly (surf, [pts], [int(x * 0.85) for x in color])
                cv2.polylines(surf, [pts], True, color, 2)

        if not low_perf:
            surf = self._apply_glow_scanlines(surf, is_light)
        return Image.fromarray(cv2.cvtColor(surf, cv2.COLOR_BGR2RGB))

    # --- допоміжні ---------------------------------------------------------

    def _hex_pts(self, cx: int, cy: int) -> np.ndarray:
        pts = [
            [cx + self.R_HEX * np.cos(np.deg2rad(60 * i + 30)),
             cy + self.R_HEX * np.sin(np.deg2rad(60 * i + 30))]
            for i in range(6)
        ]
        return np.array(pts, np.int32)

    @staticmethod
    def _apply_glow_scanlines(surf: np.ndarray, is_light: bool) -> np.ndarray:
        p_h, p_w = surf.shape[:2]
        surf_blur = cv2.GaussianBlur(surf, (3, 3), 0)

        if is_light:
            glow = cv2.GaussianBlur(surf, (45, 45), 0)
            surf = cv2.addWeighted(surf_blur, 0.85, glow, 0.25, 0)
            scanlines = np.ones((p_h, p_w, 3), dtype=np.uint8) * 255
            scanlines[::2] = 240
        else:
            glow = cv2.GaussianBlur(surf_blur, (45, 45), 0)
            surf = cv2.addWeighted(surf_blur, 1.0, glow, 0.75, 0)
            scanlines = np.ones((p_h, p_w, 3), dtype=np.uint8) * 255
            scanlines[::2] = 80

        surf = cv2.multiply(
            surf.astype(np.float32) / 255.0,
            scanlines.astype(np.float32) / 255.0
        )
        return (surf * 255).astype(np.uint8)
