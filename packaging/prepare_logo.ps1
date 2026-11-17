param([Parameter(Mandatory=$true)][string]$SourceImage, [string]$AssetPrefix='manual-logo-20261029')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$assetRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'frontend\assets'
$sourceCopy = Join-Path $assetRoot ($AssetPrefix + '-source.png')
Copy-Item -LiteralPath $SourceImage -Destination $sourceCopy -Force
$sourceBitmap = [Drawing.Bitmap]::FromFile($sourceCopy)
try {
    $iconImages = @()
    foreach ($size in @(16,24,32,48,64,128,256,512)) {
        $bitmap = New-Object Drawing.Bitmap($size,$size,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.Clear([Drawing.Color]::Transparent)
            $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $ratio = [Math]::Min($size/$sourceBitmap.Width,$size/$sourceBitmap.Height)
            $width = [int][Math]::Round($sourceBitmap.Width*$ratio)
            $height = [int][Math]::Round($sourceBitmap.Height*$ratio)
            $graphics.DrawImage($sourceBitmap,[int](($size-$width)/2),[int](($size-$height)/2),$width,$height)
            if ($size -eq 512) {
                $bitmap.Save((Join-Path $assetRoot ($AssetPrefix + '.png')),[Drawing.Imaging.ImageFormat]::Png)
            } else {
                $stream = New-Object IO.MemoryStream
                try {
                    $bitmap.Save($stream,[Drawing.Imaging.ImageFormat]::Png)
                    $iconImages += [pscustomobject]@{Size=$size;Bytes=$stream.ToArray()}
                } finally { $stream.Dispose() }
            }
        } finally { $graphics.Dispose();$bitmap.Dispose() }
    }
    $iconPath = Join-Path $assetRoot ($AssetPrefix + '.ico')
    $file = [IO.File]::Create($iconPath)
    $writer = New-Object IO.BinaryWriter($file)
    try {
        $writer.Write([uint16]0);$writer.Write([uint16]1);$writer.Write([uint16]$iconImages.Count)
        $offset = 6 + 16*$iconImages.Count
        foreach ($entry in $iconImages) {
            $dimension = if($entry.Size -eq 256){0}else{$entry.Size}
            $writer.Write([byte]$dimension);$writer.Write([byte]$dimension)
            $writer.Write([byte]0);$writer.Write([byte]0)
            $writer.Write([uint16]1);$writer.Write([uint16]32)
            $writer.Write([uint32]$entry.Bytes.Length);$writer.Write([uint32]$offset)
            $offset += $entry.Bytes.Length
        }
        foreach ($entry in $iconImages) { $writer.Write([byte[]]$entry.Bytes) }
    } finally { $writer.Dispose() }
    Write-Output $iconPath
} finally { $sourceBitmap.Dispose() }
