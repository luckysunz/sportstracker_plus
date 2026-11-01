"""Matrix output (hzeller rpi-rgb-led-matrix). Only this module imports ``rgbmatrix``."""

from __future__ import annotations

from typing import Any, Dict

from .errors import ConfigError


class MatrixDisplay:
    def __init__(self, matrix_config: Dict[str, Any]) -> None:
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions  # type: ignore
        except ImportError as exc:
            raise ConfigError("rgbmatrix not installed (use the venv with the matrix bindings): %s" % exc)
        c = matrix_config
        options = RGBMatrixOptions()
        options.rows = int(c["rows"])
        options.cols = int(c["cols"])
        options.chain_length = int(c["chain_length"])
        options.parallel = int(c["parallel"])
        options.hardware_mapping = str(c["hardware_mapping"])
        options.gpio_slowdown = int(c["gpio_slowdown"])
        options.brightness = int(c["brightness"])
        options.pwm_bits = int(c["pwm_bits"])
        options.drop_privileges = False
        self.width = options.cols * options.chain_length
        self.height = options.rows * options.parallel
        self._matrix = RGBMatrix(options=options)
        self._canvas = self._matrix.CreateFrameCanvas()

    def show(self, image) -> None:
        """Show an RGB image of exactly the matrix size (double buffered, swapped on vsync)."""
        if image.size != (self.width, self.height):
            raise ValueError("image size %s does not match matrix %dx%d" % (image.size, self.width, self.height))
        self._canvas.SetImage(image.convert("RGB"))
        self._canvas = self._matrix.SwapOnVSync(self._canvas)

    def clear(self) -> None:
        self._matrix.Clear()
