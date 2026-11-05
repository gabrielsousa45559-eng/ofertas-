$ErrorActionPreference = 'Stop'
$Project = $PSScriptRoot
$Compose = Join-Path $Project 'docker-compose.yml'
$Log = Join-Path $Project 'launcher.log'
$EnvFile = Join-Path $Project '.env'
$AppUrl = 'http://localhost:8000'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

function Write-Log([string]$Message) {
  $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
  Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
  if ($script:StatusLabel -and -not $script:StatusLabel.IsDisposed) { $script:StatusLabel.Text = $Message }
}

function Open-TextFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType File -Path $Path -Force | Out-Null }
  Start-Process notepad.exe -ArgumentList @($Path)
}

function Open-InstallGuide {
  $guide = Join-Path $Project 'INSTALAR-DOCKER.txt'
  if (-not (Test-Path -LiteralPath $guide)) {
    @'
COMO INSTALAR O DOCKER DESKTOP

1. Baixe o Docker Desktop para Windows em https://www.docker.com/products/docker-desktop/
2. Execute o instalador e mantenha a opção de usar WSL 2.
3. Reinicie o Windows se o instalador solicitar.
4. Abra Docker Desktop e aguarde aparecer "Engine running".
5. Volte ao launcher Oferta Central e clique em "Iniciar tudo".

O primeiro início baixa as imagens necessárias e pode levar alguns minutos.
'@ | Set-Content -LiteralPath $guide -Encoding UTF8
  }
  Start-Process notepad.exe -ArgumentList @($guide)
}

function Publish-ToGitHub {
  $git = Get-Command git -ErrorAction SilentlyContinue
  if (-not $git) { [System.Windows.Forms.MessageBox]::Show('Instale o Git for Windows antes de publicar.','Git não encontrado','OK','Warning') | Out-Null; return }
  $insideRepo = git -C $Project rev-parse --is-inside-work-tree 2>$null
  if ($LASTEXITCODE -ne 0 -or $insideRepo -ne 'true') {
    [System.Windows.Forms.MessageBox]::Show('Esta pasta ainda não está conectada a um repositório Git. Clone seu repositório GitHub nesta pasta ou configure o remoto antes de publicar.','Repositório não configurado','OK','Information') | Out-Null
    return
  }
  $remote = git -C $Project remote get-url origin 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $remote -or $remote -match 'SEU-USUARIO|seu-usuario|example\.com') {
    [System.Windows.Forms.MessageBox]::Show('Configure um repositório GitHub real como remoto origin antes de publicar. Nenhum arquivo foi enviado.','Destino GitHub não configurado','OK','Warning') | Out-Null
    return
  }
  $branch = git -C $Project branch --show-current
  if (-not $branch) { $branch = 'main' }
  git -C $Project add --all
  if ($LASTEXITCODE -ne 0) { [System.Windows.Forms.MessageBox]::Show('Não foi possível preparar os arquivos. Veja o log.','Erro Git','OK','Error') | Out-Null; return }
  $changes = git -C $Project status --short
  if (-not $changes) { [System.Windows.Forms.MessageBox]::Show('Não há alterações para publicar.','GitHub','OK','Information') | Out-Null; return }
  $preview = ($changes | Select-Object -First 18) -join "`r`n"
  $answer = [System.Windows.Forms.MessageBox]::Show("Destino: $remote`r`nBranch: $branch`r`n`r`nArquivos preparados:`r`n$preview`r`n`r`nConfirma criar um commit e enviar ao GitHub?",'Confirmar publicação','YesNo','Question')
  if ($answer -ne 'Yes') { return }
  $message = 'Atualizacao Oferta Central ' + (Get-Date -Format 'yyyy-MM-dd HH:mm')
  git -C $Project commit -m $message
  if ($LASTEXITCODE -ne 0) { [System.Windows.Forms.MessageBox]::Show('O commit falhou. Verifique a identidade Git (nome/email) e tente novamente.','Erro no commit','OK','Error') | Out-Null; return }
  git -C $Project push -u origin $branch
  if ($LASTEXITCODE -ne 0) { [System.Windows.Forms.MessageBox]::Show('Commit criado localmente, mas o envio falhou. Verifique login/permissão GitHub e o log do launcher.','Envio não concluído','OK','Warning') | Out-Null; return }
  [System.Windows.Forms.MessageBox]::Show('Projeto enviado ao GitHub com sucesso.','GitHub','OK','Information') | Out-Null
}

function Test-DockerReady {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
  docker info *> $null
  return ($LASTEXITCODE -eq 0)
}

function Open-Service([string]$Service) {
  Write-Log "Solicitando inicialização: $Service"
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $answer = [System.Windows.Forms.MessageBox]::Show('Docker Desktop não está instalado. Deseja abrir a página oficial para instalar?','Docker necessário','YesNo','Information')
    if ($answer -eq 'Yes') { Start-Process 'https://www.docker.com/products/docker-desktop/' }
    return
  }
  if (-not (Test-DockerReady)) {
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $desktop) { Start-Process -FilePath $desktop; $script:StatusLabel.Text = 'Abrindo Docker Desktop; aguardando o motor...'; $script:DockerWaitTimer.Start(); return }
    [System.Windows.Forms.MessageBox]::Show('Abra o Docker Desktop e aguarde o motor ficar pronto.','Docker não iniciado','OK','Warning') | Out-Null
    return
  }
  Start-DockerCompose
}

function Start-DockerCompose {
  if ($script:ComposeProcess -and -not $script:ComposeProcess.HasExited) { Write-Log 'Build já está em execução.'; return }
  Write-Log 'Montando imagem e iniciando aplicação + IA Ollama. A primeira vez baixa o modelo llama3.2.'
  $out = Join-Path $Project 'docker-compose-output.log'
  $err = Join-Path $Project 'docker-compose-error.log'
  $script:ComposeProcess = Start-Process -FilePath 'docker' -ArgumentList @('compose','up','-d','--build') -WorkingDirectory $Project -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
  $script:StatusLabel.Text = 'Construindo e baixando componentes; veja Abrir log. A primeira vez pode demorar.'
  $script:PollTimer.Start()
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Oferta Central — Inicializador'
$form.StartPosition = 'CenterScreen'
$form.ClientSize = New-Object System.Drawing.Size(650, 600)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(247,249,252)
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Oferta Central'
$title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 25)
$title.Location = New-Object System.Drawing.Point(34, 23)
$title.Size = New-Object System.Drawing.Size(580, 43)
$form.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = 'Escolha o que deseja abrir. Os serviços compartilhados iniciam juntos.'
$subtitle.Location = New-Object System.Drawing.Point(36, 78)
$subtitle.Size = New-Object System.Drawing.Size(580, 25)
$form.Controls.Add($subtitle)

function Add-LauncherButton([string]$Text, [int]$X, [int]$Y, [int]$W, [int]$H, [scriptblock]$Action) {
  $button = New-Object System.Windows.Forms.Button
  $button.Text = $Text
  $button.Location = New-Object System.Drawing.Point($X, $Y)
  $button.Size = New-Object System.Drawing.Size($W, $H)
  $button.FlatStyle = 'Flat'
  $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(45,49,58)
  $button.BackColor = [System.Drawing.Color]::White
  $button.Add_Click($Action)
  $form.Controls.Add($button)
}

Add-LauncherButton 'Start - Painel Admin' 34 126 284 48 { Open-Service 'all' }
Add-LauncherButton 'Start - API Backend' 330 126 286 48 { Open-Service 'all' }
Add-LauncherButton 'Start - Banco e Agendador' 34 188 284 48 { Open-Service 'all' }
Add-LauncherButton 'Start - Abrir painel no navegador' 330 188 286 48 { Start-Process $AppUrl }
Add-LauncherButton 'Start - Abrir tudo' 34 250 582 48 { Open-Service 'all' }
Add-LauncherButton 'Abrir .env (APIs)' 34 317 180 47 { Open-TextFile $EnvFile }
Add-LauncherButton 'Como instalar Docker' 229 317 190 47 { Open-InstallGuide }
Add-LauncherButton 'Abrir log' 434 317 182 47 { Open-TextFile $Log; Open-TextFile (Join-Path $Project 'docker-compose-error.log'); Open-TextFile (Join-Path $Project 'docker-compose-output.log') }
Add-LauncherButton 'Publicar no GitHub' 34 378 582 47 { Publish-ToGitHub }

$script:StatusLabel = New-Object System.Windows.Forms.Label
$script:StatusLabel.Text = 'Verificando Docker Desktop...'
$script:StatusLabel.Location = New-Object System.Drawing.Point(36, 440)
$script:StatusLabel.Size = New-Object System.Drawing.Size(575, 44)
$form.Controls.Add($script:StatusLabel)

$hint = New-Object System.Windows.Forms.Label
$hint.Text = 'O primeiro início cria a imagem local. O painel abre em http://localhost:8000.'
$hint.ForeColor = [System.Drawing.Color]::FromArgb(95,100,112)
$hint.Location = New-Object System.Drawing.Point(36, 505)
$hint.Size = New-Object System.Drawing.Size(580, 32)
$form.Controls.Add($hint)

$script:PollTimer = New-Object System.Windows.Forms.Timer
$script:PollTimer.Interval = 2500
$script:PollTimer.Add_Tick({
  try {
    $response = Invoke-WebRequest -Uri "$AppUrl/api/health" -TimeoutSec 2 -UseBasicParsing
    if ($response.StatusCode -eq 200) {
      $script:PollTimer.Stop()
      $script:StatusLabel.Text = 'Sistema pronto. Painel: http://localhost:8000 | IA local: carregando modelo'
      Write-Log 'API pronta em http://localhost:8000'
      Start-Process $AppUrl
    }
  } catch {
    if ($script:ComposeProcess -and $script:ComposeProcess.HasExited -and $script:ComposeProcess.ExitCode -ne 0) {
      $script:PollTimer.Stop()
      $script:StatusLabel.Text = 'Inicialização falhou. Clique em Abrir log para ver o erro.'
      Write-Log "docker compose terminou com código $($script:ComposeProcess.ExitCode)."
    }
  }
})

$script:LaunchTimer = New-Object System.Windows.Forms.Timer
$script:LaunchTimer.Add_Tick({
  $script:LaunchTimer.Stop()
  Write-Log 'Comando de inicialização enviado ao Docker Compose.'
  $script:StatusLabel.Text = 'Docker iniciando a aplicação... aguardando resposta da API.'
})

$script:DockerWaitTimer = New-Object System.Windows.Forms.Timer
$script:DockerWaitTimer.Interval = 4000
$script:DockerWaitTimer.Add_Tick({
  if (Test-DockerReady) { $script:DockerWaitTimer.Stop(); Write-Log 'Docker ficou pronto.'; Start-DockerCompose }
})

if (-not (Test-Path -LiteralPath $EnvFile)) {
  @'
# Opcional: variáveis do ambiente de execução local
TZ=America/Sao_Paulo
CORS_ORIGINS=http://localhost:5173
'@ | Set-Content -LiteralPath $EnvFile -Encoding UTF8
}
if (Test-DockerReady) { $script:StatusLabel.Text = 'Docker Desktop pronto. Selecione um serviço para iniciar.'; Write-Log 'Docker Desktop disponível.' }
elseif (Get-Command docker -ErrorAction SilentlyContinue) { $script:StatusLabel.Text = 'Docker encontrado; aguardando o Docker Desktop iniciar.'; Write-Log 'Docker CLI encontrado, daemon ainda indisponível.' }
else { $script:StatusLabel.Text = 'Docker Desktop não encontrado. Use “Como instalar Docker”.'; Write-Log 'Docker CLI não encontrado.' }

[void]$form.ShowDialog()
