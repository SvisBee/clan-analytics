Set-StrictMode -Version Latest

function ConvertTo-NativeProcessArgument {
    param([Parameter(Mandatory = $true)][string] $Value)
    if ($Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $quoted = '"'
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) { $backslashes++; continue }
        if ($character -eq [char]34) {
            $quoted += ('\' * (($backslashes * 2) + 1) -join '') + '"'
            $backslashes = 0
            continue
        }
        $quoted += ('\' * $backslashes -join '') + $character
        $backslashes = 0
    }
    $quoted += ('\' * ($backslashes * 2) -join '') + '"'
    return $quoted
}

function ConvertTo-SafeNativeDiagnostic {
    param([AllowEmptyString()][string] $Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    $safe = $Text
    $safe = $safe -replace '(?i)(authorization|token)\s*[:=]\s*\S+', '$1=[REDACTED]'
    $safe = $safe -replace '(?i)bearer\s+\S+', 'Bearer [REDACTED]'
    $safe = $safe -replace '#[A-Z0-9]{3,}', '[REDACTED_TAG]'
    $safe = $safe -replace '(?i)[a-z]:\\[^\s"'']+', '[REDACTED_PATH]'
    $safe = $safe -replace '(?s)\{\s*"(?:members|attacks|clan|opponent)".*?\}', '[REDACTED_PAYLOAD]'
    $safe = ($safe -replace '\s+', ' ').Trim()
    if ($safe.Length -gt 1000) { $safe = $safe.Substring(0, 1000) + ' [truncated]' }
    return $safe
}

function Invoke-NativeProcess {
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string] $FilePath,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeProcessArgument -Value $_ }) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "Unable to start native process: $FilePath" }
    try {
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        return [pscustomobject]@{
            process_exit_code = $process.ExitCode
            succeeded = ($process.ExitCode -eq 0)
            stdout = $stdout
            stderr_safe = ConvertTo-SafeNativeDiagnostic -Text $stderr
            had_stderr = -not [string]::IsNullOrWhiteSpace($stderr)
        }
    }
    finally { $process.Dispose() }
}
