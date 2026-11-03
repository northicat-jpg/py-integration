@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/4] ensure pyinstaller...
python -m pip install -q "pyinstaller>=6.0"

echo [2/4] build RCSBridge.exe ...
python -m PyInstaller --noconfirm --clean --onefile --console ^
  --name RCSBridge ^
  --paths src ^
  --additional-hooks-dir packaging\hooks ^
  --hidden-import config_loader ^
  --hidden-import rcs_client ^
  --hidden-import tcp_parser ^
  --hidden-import workflow ^
  --hidden-import route_resolve ^
  --hidden-import bridge ^
  --distpath dist ^
  --workpath build\pyi ^
  --specpath build\pyi ^
  main.py
if errorlevel 1 goto :fail

echo [3/4] build RCSConfigEditor.exe ...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name RCSConfigEditor ^
  --paths config_editor ^
  --hidden-import config_io ^
  --hidden-import gui ^
  --distpath dist ^
  --workpath build\pyi ^
  --specpath build\pyi ^
  config_editor\main.py
if errorlevel 1 goto :fail

echo [4/4] copy config next to exes...
if not exist "dist\config" mkdir "dist\config"
copy /Y "config\config.example.config" "dist\config\" >nul
copy /Y "config\payload_arrive.json" "dist\config\" >nul
copy /Y "config\payload_leave.json" "dist\config\" >nul
if not exist "dist\config\config.config" (
  copy /Y "config\config.example.config" "dist\config\config.config" >nul
)

echo.
echo Done. Output:
echo   dist\RCSBridge.exe
echo   dist\RCSConfigEditor.exe
echo   dist\config\
echo.
echo Tip: edit routes with RCSConfigEditor.exe; run bridge with RCSBridge.exe
goto :eof

:fail
echo BUILD FAILED
exit /b 1
