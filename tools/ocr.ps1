param([Parameter(Mandatory=$true)][string]$Path, [string]$Lang = "")
# Offline OCR. Prefers Tesseract (Hebrew+English) when installed; falls back to Windows built-in OCR (Windows.Media.Ocr).
# Tesseract language data: $env:WA_TESSDATA, or %USERPROFILE%\tools\tessdata (heb/eng .traineddata).
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$tess = @("$env:ProgramFiles\Tesseract-OCR\tesseract.exe", "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($tess -and $Lang -ne "windows") {
    $td = if ($env:WA_TESSDATA) { $env:WA_TESSDATA } else { Join-Path $env:USERPROFILE "tools\tessdata" }
    $l = if ($Lang) { $Lang } else { "heb+eng" }
    $args2 = @((Resolve-Path $Path).Path, "-", "-l", $l)
    if (Test-Path (Join-Path $td "heb.traineddata")) { $args2 += @("--tessdata-dir", $td) }
    $ErrorActionPreference = "Continue"  # tesseract prints progress on stderr
    & $tess @args2 2>$null | Out-String | Write-Output
    exit $LASTEXITCODE
}
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$engine = $null
if ($Lang -and $Lang -ne "windows") { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang)) }
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { Write-Error "No OCR language installed (Settings > Time & language > Language > add a language with OCR support)"; exit 2 }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Path).Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
($res.Lines | ForEach-Object { $_.Text }) -join "`n"
