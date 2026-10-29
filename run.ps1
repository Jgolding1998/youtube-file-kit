# Creates .venv on first run (exact pinned, hash-checked packages), then starts the app.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$pythonPath = ".\.venv\Scripts\python.exe"
$createdEnvironment = $false
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $createdEnvironment = $true
}
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $requirementsBytes = [System.IO.File]::ReadAllBytes((Join-Path $PSScriptRoot "requirements.txt"))
    $requirementsHash = [System.BitConverter]::ToString($sha256.ComputeHash($requirementsBytes)).Replace("-", "").ToLowerInvariant()
} finally {
    $sha256.Dispose()
}
$installedHash = ""
if (Test-Path -LiteralPath ".venv\.installed" -PathType Leaf) {
    $installedHash = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot ".venv\.installed"))
    $installedHash = $installedHash.Trim()
}
if ($createdEnvironment -or $installedHash -ne $requirementsHash) {
    & $pythonPath -m pip install --require-hashes --only-binary=:all: -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Set-Content -LiteralPath ".venv\.installed" -Value $requirementsHash -Encoding ascii
}
& $pythonPath -m youtube_file_kit @args
exit $LASTEXITCODE
