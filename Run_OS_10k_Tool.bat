@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw os10k_gui.py) || (start "" pyw -3 os10k_gui.py)
