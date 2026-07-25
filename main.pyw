"""
main.pyw
========
Точка входу HEX_MC. core.config виконує ініціалізацію CONF/THEMES
одразу при імпорті (див. core/config.py), тому тут достатньо просто
підняти рушій.
"""
import sys
import os

# Дозволяє запускати "python main.pyw" з будь-якої робочої директорії.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.app import MatrixApp

if __name__ == "__main__":
    app = MatrixApp()
    app.mainloop()
