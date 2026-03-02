@echo off
cd /d "%~dp0"
pip install -q -r requirements.txt 2>nul
streamlit run app.py
pause
