@echo off
title DEUS 3.0 - Multi-Agent Outreach Intelligence
cd /d "%~dp0"
"C:\Users\HP\AppData\Local\Python\bin\python3.14-64.exe" -c "import sys; sys.path.insert(0, '.'); from deus_gui import DeusGUI; app = DeusGUI(); app.run()"
pause
