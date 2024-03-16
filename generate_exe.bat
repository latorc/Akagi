REM Convert the python project into executable using pyinstaller
set PLAYWRIGHT_BROWSERS_PATH=0
playwright install chromium

rmdir /s /q dist
pyinstaller client.py
REM rename dist\main\client.exe akagi.exe
robocopy .\ dist\client\ *.json
robocopy .\ dist\client\_internal\ *.tcss
robocopy .\liqi_proto dist\client\_internal\liqi_proto /E
robocopy .venv\Lib\site-packages\playwright\driver\package\.local-browsers dist\client\_internal\playwright\driver\package\.local-browsers /E
rename dist\client akagi
explorer.exe dist\akagi