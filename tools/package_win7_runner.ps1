param(
    [string]$OutDir = "C:\Users\mmq8658\Desktop\Microprobe\win7_runner_packages"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$PackageRoot = Join-Path $OutDir "Microprobe_Python_win7_runner_$Timestamp"
$ZipPath = "$PackageRoot.zip"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null

$ExcludeDirs = @(
    ".venv",
    ".venv_win7",
    "__pycache__",
    ".pytest_cache",
    "results",
    "result",
    "run_logs"
)

$ExcludeFilePatterns = @(
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.zip"
)

Get-ChildItem -Path $ProjectDir -Force | ForEach-Object {
    $name = $_.Name
    if ($_.PSIsContainer -and ($ExcludeDirs -contains $name)) {
        return
    }
    $destination = Join-Path $PackageRoot $name
    if ($_.PSIsContainer) {
        robocopy $_.FullName $destination /E /XD $ExcludeDirs /XF $ExcludeFilePatterns | Out-Null
        if ($LASTEXITCODE -gt 7) {
            throw "robocopy failed for $($_.FullName) with exit code $LASTEXITCODE"
        }
    } else {
        $skip = $false
        foreach ($pattern in $ExcludeFilePatterns) {
            if ($name -like $pattern) {
                $skip = $true
                break
            }
        }
        if (-not $skip) {
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }
}

if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $ZipPath -Force

Write-Host "Created runner folder: $PackageRoot"
Write-Host "Created runner zip:    $ZipPath"
