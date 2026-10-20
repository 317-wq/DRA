# Windows 版演示脚本：依次跑三个主题，最后列出全部 run。
# 用法（仓库目录内）：powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$installed = Get-Command dra -ErrorAction SilentlyContinue
function Invoke-Dra {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($installed) {
        & dra @Arguments
    } else {
        & uv run dra @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw "dra 退出码 $LASTEXITCODE" }
}

$topics = @(
    "2026 年主流 Agent 记忆机制方案对比",
    "2026 年 RAG 与长上下文方案的选择边界",
    "2026 年中国新能源汽车换电模式的现状与争议"
)

foreach ($topic in $topics) {
    Write-Host "==> $topic" -ForegroundColor Cyan
    Invoke-Dra run $topic --max-sources 8
}

Invoke-Dra list
