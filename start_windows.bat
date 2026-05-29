@echo off
title Uber AI Cloud V14 Professional OCR
echo Starting Uber AI Cloud V14 Professional OCR...
echo Current folder:
cd
echo.
python -m pip install -r requirements.txt
echo.
echo Opening browser...
start http://127.0.0.1:5000
python app.py
pause
