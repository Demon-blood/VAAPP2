$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root 'backend'
Push-Location $Backend
try {
    if (-not (Test-Path '.env')) {
        Copy-Item '.env.example' '.env'
        $Fernet = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>$null
        if (-not $Fernet) {
            $bytes = New-Object byte[] 32
            [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
            $Fernet = [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_')
        }
        $Pair = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
        (Get-Content '.env') `
            -replace '^TOKEN_ENCRYPTION_KEY=.*$', "TOKEN_ENCRYPTION_KEY=$Fernet" `
            -replace '^PAIRING_SECRET=.*$', "PAIRING_SECRET=$Pair" | Set-Content '.env'
        Write-Host 'Created backend\.env. Complete the Google, AI, public URL, and Enable Banking values before production use.' -ForegroundColor Yellow
    }

    if (-not (Test-Path '.venv')) { python -m venv .venv }
    & '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
    & '.\.venv\Scripts\python.exe' -m pip install -e .
    & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 0.0.0.0 --port 8080
}
finally {
    Pop-Location
}
