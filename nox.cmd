@echo off
REM ===========================================================================
REM  nox - inicia o Exponexa (pacote Python `nox`) de qualquer diretorio.
REM
REM  Nao usa shell dinamico, nao cria nem le chave de API, nao toca na
REM  autenticacao da CLI do Claude e nao inicia nenhum processo de login: apenas
REM  chama o Python do .venv com `-m nox`, repassando os argumentos recebidos.
REM
REM  O PYTHONPATH aponta para a raiz do projeto para o pacote ser encontrado
REM  sem precisar mudar o diretorio atual do usuario.
REM ===========================================================================

setlocal
set "NOX_HOME=%~dp0"
if "%NOX_HOME:~-1%"=="\" set "NOX_HOME=%NOX_HOME:~0,-1%"

set "NOX_PYTHON=%NOX_HOME%\.venv\Scripts\python.exe"
if not exist "%NOX_PYTHON%" (
    echo [nox] Python do ambiente virtual nao encontrado em:
    echo       %NOX_PYTHON%
    echo       Crie o .venv ou ajuste o caminho neste launcher.
    exit /b 1
)

if defined PYTHONPATH (
    set "PYTHONPATH=%NOX_HOME%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%NOX_HOME%"
)

"%NOX_PYTHON%" -m nox %*
exit /b %ERRORLEVEL%
