Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = 'C:/ProgramData/anaconda3/python.exe'

function Invoke-GateStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host "==> $Label" -ForegroundColor Cyan
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Release gate step failed: $Label"
    }
}

Push-Location $repoRoot
try {
    $env:PYTHONPATH = 'src'

    Invoke-GateStep -Label 'Full unittest regression' -Arguments @('-m', 'unittest', 'discover', '-s', 'test', '-v')
    # Note: orchestration regression was merged into full regression in Step 8
    Invoke-GateStep -Label 'Release docs validation' -Arguments @('-m', 'unittest', 'discover', '-s', 'test', '-p', 'test_release_gate_docs.py', '-v')

    Invoke-GateStep -Label 'CLI smoke: agent --help' -Arguments @('./src/main.py', 'agent', '--help')
    Invoke-GateStep -Label 'CLI smoke: agent-chat --help' -Arguments @('./src/main.py', 'agent-chat', '--help')
    Invoke-GateStep -Label 'CLI smoke: agent-resume --help' -Arguments @('./src/main.py', 'agent-resume', '--help')

    Write-Host 'Release gate passed.' -ForegroundColor Green
}
finally {
    Pop-Location
}