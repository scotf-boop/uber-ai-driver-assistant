@echo off
chcp 65001 >nul
title Uber AI Pro V3.1
echo 正在启动 Uber AI Pro V3.1...
python --version >nul 2>&1
if errorlevel 1 (
  echo 没检测到 Python。请先安装 Python。
  pause
  exit /b
)
pip install flask pillow werkzeug rapidocr-onnxruntime >nul 2>&1
start http://127.0.0.1:5000
python app.py
pause
