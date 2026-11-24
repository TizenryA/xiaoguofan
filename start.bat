@echo off
rem Windows launcher kept for the release package layout (the CI package step copies it next to aistudio2api.exe).
setlocal
pushd "%~dp0"
"%CD%\aistudio2api.exe" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
