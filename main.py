"""Raman microscope control panel.

Usage:
    python main.py            # uses config.toml
    python main.py --sim      # everything simulated, to practise without hardware
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Raman microscope control panel")
    parser.add_argument("--config", default=str(HERE / "config.toml"), help="configuration file")
    parser.add_argument("--sim", action="store_true", help="simulate all instruments")
    args = parser.parse_args()

    from raman_control.config import load_config
    config_path = Path(args.config)
    cfg = load_config(config_path if config_path.is_file() else None, force_simulation=args.sim)

    log_file = Path(cfg["general"]["log_file"])
    if not log_file.is_absolute():
        log_file = HERE / log_file
    logging.basicConfig(filename=log_file, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")

    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

    from raman_control.gui.main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("Raman Control")
    window = MainWindow(cfg)
    window.show_windows()
    if not config_path.is_file():
        window.log("warn", f"Cannot find {config_path}; using default values (everything simulated).")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
