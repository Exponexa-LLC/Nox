<#
.SYNOPSIS
    Instalador do Exponexa (comando `nox`) para Windows x64.

.DESCRIPTION
    Baixa uma release oficial versionada de Exponexa-LLC/Nox, confere o
    SHA-256 ANTES de extrair qualquer coisa e instala no diretorio do
    usuario. Nao pede administrador, nao grava credencial, nao envia
    telemetria e nao toca na sua configuracao.

    O que este script NUNCA faz:
      - ler, escrever ou apagar ~/.nox e ~/.delet_user (sua configuracao e
        sua; a limpeza desses diretorios fica para uma etapa futura, e hoje
        nao existe parametro que a execute);
      - alterar o PATH da maquina (HKLM) ou exigir privilegio;
      - extrair ou executar o download antes de o checksum conferir;
      - autenticar voce em qualquer servico ou chamar o modelo.

.PARAMETER Version
    Versao a instalar, como 0.7.0. Sem ela, usa $env:NOX_VERSION; sem as
    duas, consulta a ultima release publicada.

.PARAMETER Prefix
    Raiz da instalacao. Padrao: %LOCALAPPDATA%\Programs\Exponexa

.PARAMETER Source
    Origem alternativa dos arquivos (URL ou pasta local). Serve para testes
    offline e para instalar de um espelho interno.

.PARAMETER DryRun
    Mostra o plano e nao escreve absolutamente nada.

.PARAMETER AddToPath
    Acrescenta o diretorio do shim ao PATH do USUARIO. Sem este parametro, o
    script pergunta (quando ha terminal) ou apenas imprime a linha a colar.

.PARAMETER Uninstall
    Remove as versoes instaladas, o shim e a entrada de PATH criada aqui.
    Preserva toda a configuracao do usuario.

.PARAMETER ListVersions
    Lista as versoes instaladas e qual esta ativa.

.EXAMPLE
    irm https://raw.githubusercontent.com/Exponexa-LLC/Nox/main/install.ps1 | iex

.EXAMPLE
    & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Exponexa-LLC/Nox/main/install.ps1))) -AddToPath
#>

[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Prefix = "",
    [string]$Source = "",
    [switch]$DryRun,
    [switch]$AddToPath,
    [switch]$Uninstall,
    [switch]$ListVersions
)

$ErrorActionPreference = "Stop"

# O Windows PowerShell 5.1 pode negociar TLS 1.0 por padrao, e o GitHub recusa
# a conexao. Acrescentamos TLS 1.2 (e 1.3, quando o .NET desta maquina o
# conhece) ao que ja esta configurado, sem remover nada. No PowerShell 7 isto
# e inofensivo: ele ja negocia versoes modernas.
try {
    $protocolos = [Net.ServicePointManager]::SecurityProtocol
    foreach ($nome in @("Tls12", "Tls13")) {
        if ([enum]::GetNames([Net.SecurityProtocolType]) -contains $nome) {
            $protocolos = $protocolos -bor [Net.SecurityProtocolType]::$nome
        }
    }
    [Net.ServicePointManager]::SecurityProtocol = $protocolos
} catch {
    # ambiente sem ServicePointManager: seguimos com o padrao do host
}

# ---------------------------------------------------------------- constantes

$RepoOwner = "Exponexa-LLC"
$RepoName = "Nox"
$ReleasesApi = "https://api.github.com/repos/$RepoOwner/$RepoName/releases"
$ReleasesDownload = "https://github.com/$RepoOwner/$RepoName/releases/download"
$ChecksumFile = "SHA256SUMS"
$ShimName = "nox.cmd"

# ------------------------------------------------------------------ saida

function Write-Passo([string]$texto) { Write-Host "  $texto" }
function Write-Titulo([string]$texto) { Write-Host ""; Write-Host $texto }
function Write-Aviso([string]$texto) { Write-Host "  ! $texto" -ForegroundColor Yellow }
function Write-Erro([string]$texto) { Write-Host "  x $texto" -ForegroundColor Red }

function Stop-Com([string]$mensagem) {
    Write-Erro $mensagem
    exit 1
}

# ------------------------------------------------------------- plataforma

function Test-EhWindows {
    <#  `$IsWindows` so existe no PowerShell 6+. No Windows PowerShell 5.1 ele
        e `$null`, e um `-not $IsWindows` daria VERDADEIRO - foi assim que o
        instalador passou a recusar o Windows dizendo que era Linux/macOS.

        A ordem abaixo cobre os dois mundos: no PS 6+ a variavel decide (e
        acerta em Linux/macOS); no 5.1, que so existe no Windows, caimos nos
        indicadores classicos. #>
    if (Test-Path variable:IsWindows) { return [bool]$IsWindows }
    if ($env:OS -eq "Windows_NT") { return $true }
    try {
        return ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT)
    } catch {
        return $false
    }
}

function Get-Arquitetura {
    <#  `RuntimeInformation` existe no .NET Framework 4.7.1+ e no .NET moderno,
        mas nao e garantido em toda maquina com 5.1: por isso a reserva. #>
    try {
        return [string][System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
    } catch {
        if ([Environment]::Is64BitOperatingSystem) {
            if ($env:PROCESSOR_ARCHITECTURE -match "ARM") { return "Arm64" }
            return "X64"
        }
        return "X86"
    }
}

function Test-Plataforma {
    <#  So Windows x64 tem bundle publicado. Recusar com clareza e melhor
        que instalar algo que nao roda. #>
    if (-not (Test-EhWindows)) {
        Stop-Com ("este instalador e do bundle Windows. Em Linux/macOS ainda " +
                  "nao ha executavel publicado - use o pacote Python: " +
                  "pip install textual && python -m nox")
    }
    $arq = Get-Arquitetura
    if ($arq -ne "X64") {
        Stop-Com ("arquitetura $arq nao tem build publicado (so Windows x64). " +
                  "Use o pacote Python enquanto isso.")
    }
    return "windows-x64"
}

# --------------------------------------------------------------- caminhos

function Get-Prefixo([string]$informado) {
    if ($informado) { return [System.IO.Path]::GetFullPath($informado) }
    return Join-Path $env:LOCALAPPDATA "Programs\Exponexa"
}

function Get-DiretorioVersoes([string]$prefixo) { Join-Path $prefixo "nox" }
function Get-DiretorioBin([string]$prefixo) { Join-Path $prefixo "bin" }
function Get-CaminhoShim([string]$prefixo) { Join-Path (Get-DiretorioBin $prefixo) $ShimName }

function Get-VersoesInstaladas([string]$prefixo) {
    $raiz = Get-DiretorioVersoes $prefixo
    if (-not (Test-Path $raiz)) { return @() }
    return @(Get-ChildItem $raiz -Directory -ErrorAction SilentlyContinue |
             Sort-Object Name | Select-Object -ExpandProperty Name)
}

function Get-VersaoAtiva([string]$prefixo) {
    $shim = Get-CaminhoShim $prefixo
    if (-not (Test-Path $shim)) { return "" }
    $conteudo = Get-Content $shim -Raw
    $achado = [regex]::Match($conteudo, [regex]::Escape((Get-DiretorioVersoes $prefixo)) + '\\([^\\"]+)\\nox\.exe')
    if ($achado.Success) { return $achado.Groups[1].Value }
    return ""
}

# ---------------------------------------------------------------- versao

function Resolve-Versao([string]$informada) {
    if ($informada) { return ($informada -replace '^v', '') }
    if ($env:NOX_VERSION) { return ($env:NOX_VERSION -replace '^v', '') }
    Write-Passo "consultando a ultima release publicada..."
    try {
        $resposta = Invoke-RestMethod -Uri "$ReleasesApi/latest" -Headers @{
            "Accept" = "application/vnd.github+json"
            "User-Agent" = "exponexa-nox-installer"
        } -TimeoutSec 30
    } catch {
        Stop-Com ("nao consegui consultar as releases ($($_.Exception.Message)). " +
                  "Informe a versao: -Version 0.7.0  (ou `$env:NOX_VERSION)")
    }
    if (-not $resposta.tag_name) {
        Stop-Com "a API nao devolveu nenhuma release. Informe -Version explicitamente."
    }
    return ($resposta.tag_name -replace '^v', '')
}

# ------------------------------------------------------------- download

function Get-Arquivo([string]$origem, [string]$destino) {
    <#  Aceita URL ou caminho local. O caminho local existe para testes
        offline e para espelhos internos - a logica de verificacao e a
        mesma nos dois casos. #>
    if ($origem -match '^https?://') {
        # A barra de progresso do Invoke-WebRequest custa caro: com ela ligada,
        # baixar 15 MB pode levar minutos no Windows PowerShell. Silenciar e
        # local a esta funcao - o valor anterior volta no finally.
        $progressoAntes = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $origem -OutFile $destino -UseBasicParsing -TimeoutSec 120
        } finally {
            $ProgressPreference = $progressoAntes
        }
    } else {
        $local = [System.IO.Path]::GetFullPath($origem)
        if (-not (Test-Path $local)) { Stop-Com "nao encontrei: $local" }
        Copy-Item $local $destino -Force
    }
}

function Get-BaseOrigem([string]$source, [string]$versao) {
    if ($source) { return $source.TrimEnd('/', '\') }
    return "$ReleasesDownload/v$versao"
}

function Join-Origem([string]$base, [string]$nome) {
    if ($base -match '^https?://') { return "$base/$nome" }
    return (Join-Path $base $nome)
}

# ------------------------------------------------------------- checksum

function Get-HashEsperado([string]$arquivoSums, [string]$nomeZip) {
    <#  Formato do sha256sum: <hash><dois espacos><arquivo>. #>
    foreach ($linha in Get-Content $arquivoSums) {
        $limpo = $linha.Trim()
        if (-not $limpo) { continue }
        $partes = $limpo -split '\s+', 2
        if ($partes.Count -lt 2) { continue }
        if ($partes[1].Trim() -eq $nomeZip) { return $partes[0].ToLower() }
    }
    return ""
}

function Assert-Checksum([string]$zip, [string]$sums, [string]$nomeZip) {
    $esperado = Get-HashEsperado $sums $nomeZip
    if (-not $esperado) {
        Stop-Com "nao achei a linha de $nomeZip em $ChecksumFile - instalacao abortada."
    }
    $obtido = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($obtido -ne $esperado) {
        Write-Erro "o arquivo baixado NAO confere com o checksum da release."
        Write-Passo "esperado: $esperado"
        Write-Passo "obtido:   $obtido"
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Stop-Com "nada foi extraido. Baixe de novo ou verifique a origem."
    }
    Write-Passo "checksum confere: $esperado"
}

# ------------------------------------------------------------------ shim

function Write-Shim([string]$prefixo, [string]$versao) {
    $bin = Get-DiretorioBin $prefixo
    if (-not (Test-Path $bin)) { New-Item -ItemType Directory -Path $bin -Force | Out-Null }
    $alvo = Join-Path (Join-Path (Get-DiretorioVersoes $prefixo) $versao) "nox.exe"
    $conteudo = @"
@echo off
REM Shim do Exponexa - aponta para a versao ativa. Gerado por install.ps1.
"$alvo" %*
"@
    Set-Content -Path (Get-CaminhoShim $prefixo) -Value $conteudo -Encoding ascii
}

# ------------------------------------------------------------------ PATH

function Test-NoPath([string]$diretorio) {
    $atual = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $atual) { return $false }
    return ($atual -split ';' | Where-Object { $_.TrimEnd('\') -eq $diretorio.TrimEnd('\') }).Count -gt 0
}

function Add-AoPath([string]$diretorio) {
    <#  Escopo USUARIO, sempre. Nunca Machine, nunca privilegio. #>
    if (Test-NoPath $diretorio) {
        Write-Passo "o PATH do usuario ja contem $diretorio"
        return
    }
    $atual = [Environment]::GetEnvironmentVariable("Path", "User")
    $novo = if ($atual) { "$atual;$diretorio" } else { $diretorio }
    [Environment]::SetEnvironmentVariable("Path", $novo, "User")
    Write-Passo "PATH do usuario atualizado - reabra o terminal para valer."
}

function Remove-DoPath([string]$diretorio) {
    $atual = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $atual) { return }
    $restante = ($atual -split ';' | Where-Object {
        $_ -and ($_.TrimEnd('\') -ne $diretorio.TrimEnd('\'))
    }) -join ';'
    if ($restante -ne $atual) {
        [Environment]::SetEnvironmentVariable("Path", $restante, "User")
        Write-Passo "entrada removida do PATH do usuario."
    }
}

function Resolve-PermissaoPath([string]$diretorio) {
    if ($AddToPath) { return $true }
    if ($DryRun) { return $false }
    # `[Environment]::UserInteractive` continua $true sob -NonInteractive e com
    # a entrada redirecionada, entao nao da para confiar nele: tentamos
    # perguntar e, se o host recusar, seguimos SEM mexer no PATH. Nunca
    # alteramos o PATH por falta de resposta - a omissao e o lado seguro.
    Write-Host ""
    try {
        $resposta = Read-Host "  acrescentar $diretorio ao PATH do usuario? (s/N)"
    } catch {
        Write-Passo "(sem terminal interativo: o PATH fica como esta)"
        return $false
    }
    return ($resposta -match '^(s|sim|y|yes)$')
}

# ------------------------------------------------------------- diagnostico

function Show-DiagnosticoClaude {
    <#  So olha se a CLI existe. Nao autentica, nao chama o modelo, nao le
        credencial nenhuma. #>
    Write-Titulo "provedor"
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if ($claude) {
        Write-Passo "CLI do Claude encontrada: $($claude.Source)"
        Write-Passo "rode 'nox setup' para o diagnostico completo."
    } else {
        Write-Aviso "a CLI do Claude nao esta no PATH."
        Write-Passo "o Exponexa conversa atraves dela; instale a partir de"
        Write-Passo "https://claude.com/claude-code e autentique-se com 'claude auth login'."
        Write-Passo "a autenticacao e sua e local - este instalador nao pede,"
        Write-Passo "nao copia e nao guarda credencial nenhuma."
    }
}

# ------------------------------------------------------------------ acoes

function Invoke-ListVersions([string]$prefixo) {
    $versoes = Get-VersoesInstaladas $prefixo
    $ativa = Get-VersaoAtiva $prefixo
    Write-Titulo "versoes em $prefixo"
    if (-not $versoes) { Write-Passo "(nenhuma instalada)"; return }
    foreach ($v in $versoes) {
        $marca = if ($v -eq $ativa) { "* " } else { "  " }
        Write-Passo "$marca$v"
    }
    if ($ativa) { Write-Passo "" ; Write-Passo "ativa: $ativa" }
}

function Invoke-Uninstall([string]$prefixo) {
    Write-Titulo "desinstalando o Exponexa"
    $versoesDir = Get-DiretorioVersoes $prefixo
    $bin = Get-DiretorioBin $prefixo
    $shim = Get-CaminhoShim $prefixo

    Write-Passo "remover: $versoesDir"
    Write-Passo "remover: $shim"
    Write-Passo "remover a entrada de PATH: $bin"
    Write-Passo "PRESERVAR: sua configuracao (nada em ~/.nox ou ~/.delet_user e tocado)"

    if ($DryRun) { Write-Titulo "-DryRun: nada foi alterado."; return }

    if (Test-Path $shim) { Remove-Item $shim -Force }
    if (Test-Path $versoesDir) { Remove-Item $versoesDir -Recurse -Force }
    Remove-DoPath $bin
    if ((Test-Path $bin) -and -not (Get-ChildItem $bin -Force)) { Remove-Item $bin -Force }
    if ((Test-Path $prefixo) -and -not (Get-ChildItem $prefixo -Force)) { Remove-Item $prefixo -Force }
    Write-Titulo "desinstalado. Sua configuracao continua onde estava."
}

function Invoke-Install([string]$prefixo, [string]$versao, [string]$source) {
    $plataforma = Test-Plataforma
    $nomeZip = "nox-$versao-$plataforma.zip"
    $destinoVersao = Join-Path (Get-DiretorioVersoes $prefixo) $versao
    $bin = Get-DiretorioBin $prefixo

    Write-Titulo "Exponexa $versao ($plataforma)"
    Write-Passo "instalar em: $destinoVersao"
    Write-Passo "shim:        $(Get-CaminhoShim $prefixo)"

    # versao ja presente: rollback/troca e so reapontar o shim
    if ((Test-Path $destinoVersao) -and (Get-ChildItem $destinoVersao -Force -ErrorAction SilentlyContinue)) {
        Write-Passo "esta versao ja esta instalada - reapontando o shim (sem baixar nada)."
        if ($DryRun) { Write-Titulo "-DryRun: nada foi alterado."; return }
        Write-Shim $prefixo $versao
        Write-Titulo "ativa agora: $versao"
        Show-DiagnosticoClaude
        return
    }

    $base = Get-BaseOrigem $source $versao
    $origemZip = Join-Origem $base $nomeZip
    $origemSums = Join-Origem $base $ChecksumFile
    Write-Passo "origem:      $origemZip"

    if ($DryRun) {
        Write-Passo "verificaria o SHA-256 contra $ChecksumFile antes de extrair"
        if ($AddToPath) { Write-Passo "acrescentaria $bin ao PATH do usuario" }
        else { Write-Passo "nao mexeria no PATH (use -AddToPath)" }
        Write-Titulo "-DryRun: nada foi baixado, extraido ou escrito."
        return
    }

    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nox-install-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temp -Force | Out-Null
    try {
        $zip = Join-Path $temp $nomeZip
        $sums = Join-Path $temp $ChecksumFile
        Write-Passo "baixando..."
        Get-Arquivo $origemZip $zip
        Get-Arquivo $origemSums $sums

        # nada e extraido antes desta linha
        Assert-Checksum $zip $sums $nomeZip

        if (Test-Path $destinoVersao) { Remove-Item $destinoVersao -Recurse -Force }
        New-Item -ItemType Directory -Path $destinoVersao -Force | Out-Null
        Expand-Archive -Path $zip -DestinationPath $destinoVersao -Force
        Write-Passo "extraido."

        Write-Shim $prefixo $versao
        Write-Passo "shim criado."
    } finally {
        Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (Resolve-PermissaoPath $bin) { Add-AoPath $bin }
    else {
        Write-Titulo "PATH nao foi alterado."
        Write-Passo "para usar o comando de qualquer pasta, rode:"
        Write-Passo "  [Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';$bin', 'User')"
        Write-Passo "ou chame direto: $(Get-CaminhoShim $prefixo)"
    }

    Write-Titulo "Exponexa $versao instalado."
    Show-DiagnosticoClaude
}

# ------------------------------------------------------------------ main

$prefixo = Get-Prefixo $Prefix

if ($ListVersions) { Invoke-ListVersions $prefixo; exit 0 }
if ($Uninstall) { Invoke-Uninstall $prefixo; exit 0 }

$versao = Resolve-Versao $Version
Invoke-Install $prefixo $versao $Source
exit 0
