param([string]$Node = 'node', [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$nodeCommand = (Get-Command $Node -CommandType Application -ErrorAction Stop).Source
$pythonCommand = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
& $nodeCommand (Join-Path $PSScriptRoot 'offline.mjs') install --python $pythonCommand
exit $LASTEXITCODE
