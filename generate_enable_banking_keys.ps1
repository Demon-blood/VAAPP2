$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Secrets = Join-Path $Root 'backend\secrets'
New-Item -ItemType Directory -Path $Secrets -Force | Out-Null

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    throw 'OpenSSL is required. Install it and run this script again.'
}

$Key = Join-Path $Secrets 'enable_banking_private_key.pem'
$Cert = Join-Path $Secrets 'enable_banking_public_certificate.pem'
openssl genrsa -out $Key 4096
openssl req -new -x509 -days 365 -key $Key -out $Cert -subj '/C=BE/O=Full-Time VA/CN=Full-Time VA'
Write-Host "Private key: $Key" -ForegroundColor Green
Write-Host "Upload this certificate to the Enable Banking control panel: $Cert" -ForegroundColor Green
Write-Host 'Never copy the private key into the Android app or send it through chat.' -ForegroundColor Yellow
