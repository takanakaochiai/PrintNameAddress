param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [switch]$ValidateOnly,
    [long]$OwnerHandle = 0
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$pages = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$folder = Split-Path -LiteralPath $Manifest
if ($ValidateOnly) {
    foreach ($page in $pages) {
        $image = [Drawing.Image]::FromFile((Join-Path $folder $page.file))
        $image.Dispose()
    }
    exit 0
}

$document = [Drawing.Printing.PrintDocument]::new()
$dialog = [Windows.Forms.PrintDialog]::new()
try {
    $document.DocumentName = 'PrintNameAddress'
    $document.PrintController = [Drawing.Printing.StandardPrintController]::new()
    $document.DefaultPageSettings.Landscape = $pages[0].width -gt $pages[0].height
    $short = [Math]::Min($pages[0].width, $pages[0].height)
    $long = [Math]::Max($pages[0].width, $pages[0].height)
    if ($document.PrinterSettings.IsValid -and [Math]::Abs($short - 595.28) -lt 2 -and
        [Math]::Abs($long - 841.89) -lt 2) {
        $a4 = $document.PrinterSettings.PaperSizes | Where-Object Kind -eq A4 | Select-Object -First 1
        if ($a4) { $document.DefaultPageSettings.PaperSize = $a4 }
    }
    $dialog.Document = $document
    $dialog.UseEXDialog = $true
    $dialog.AllowSomePages = $true
    $dialog.PrinterSettings.MinimumPage = 1
    $dialog.PrinterSettings.MaximumPage = $pages.Count
    $dialog.PrinterSettings.FromPage = 1
    $dialog.PrinterSettings.ToPage = $pages.Count
    if ($OwnerHandle) {
        Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @'
using System;
using System.Windows.Forms;

public sealed class PrintDialogOwner : IWin32Window
{
    public PrintDialogOwner(long handle) { Handle = new IntPtr(handle); }
    public IntPtr Handle { get; private set; }
}
'@
        $result = $dialog.ShowDialog([PrintDialogOwner]::new($OwnerHandle))
    }
    else {
        $result = $dialog.ShowDialog()
    }
    if ($result -ne [Windows.Forms.DialogResult]::OK) { exit 0 }
    if (!$document.PrinterSettings.IsValid) { throw 'The selected printer is unavailable.' }

    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class PrinterDevice
{
    public const int HorizontalResolution = 8;
    public const int VerticalResolution = 10;

    [DllImport("gdi32.dll")]
    public static extern int GetDeviceCaps(IntPtr deviceContext, int index);

    [StructLayout(LayoutKind.Sequential)]
    private struct BitmapInfoHeader
    {
        public uint Size;
        public int Width, Height;
        public ushort Planes, BitCount;
        public uint Compression, SizeImage;
        public int XPelsPerMeter, YPelsPerMeter;
        public uint ClrUsed, ClrImportant;
    }

    [DllImport("gdi32.dll")]
    private static extern int StretchDIBits(IntPtr dc, int x, int y, int width, int height,
        int sourceX, int sourceY, int sourceWidth, int sourceHeight, IntPtr pixels,
        ref BitmapInfoHeader info, uint colorUse, uint rasterOperation);

    public static void DrawPage(IntPtr dc, Bitmap bitmap, int x, int y, int width, int height)
    {
        var bounds = new Rectangle(0, 0, bitmap.Width, bitmap.Height);
        BitmapData data = bitmap.LockBits(bounds, ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
        try
        {
            // Send the RGB raster directly to the printer DC in bounded horizontal bands.
            const int bandHeight = 128;
            const uint sourceCopy = 0x00CC0020;
            var info = new BitmapInfoHeader {
                Size = (uint)Marshal.SizeOf(typeof(BitmapInfoHeader)),
                Width = bitmap.Width, Planes = 1, BitCount = 24
            };
            for (int top = 0; top < bitmap.Height; top += bandHeight)
            {
                int rows = Math.Min(bandHeight, bitmap.Height - top);
                int destinationTop = y + (int)((long)top * height / bitmap.Height);
                int destinationBottom = y + (int)((long)(top + rows) * height / bitmap.Height);
                if (destinationBottom == destinationTop) continue;

                info.Height = data.Stride > 0 ? -rows : rows;
                int firstRow = data.Stride > 0 ? top : top + rows - 1;
                IntPtr pixels = IntPtr.Add(data.Scan0, firstRow * data.Stride);
                int result = StretchDIBits(dc, x, destinationTop, width, destinationBottom - destinationTop,
                    0, 0, bitmap.Width, rows, pixels, ref info, 0, sourceCopy);
                if (result == 0 || result == -1)
                    throw new InvalidOperationException("The printer could not draw the page image.");
            }
        }
        finally
        {
            bitmap.UnlockBits(data);
        }
    }
}
'@

    $progress = @{ Index = 0; Last = $pages.Count - 1 }
    if ($document.PrinterSettings.PrintRange -eq [Drawing.Printing.PrintRange]::SomePages) {
        $progress.Index = $dialog.PrinterSettings.FromPage - 1
        $progress.Last = $dialog.PrinterSettings.ToPage - 1
    }
    $document.add_QueryPageSettings({
        param($sender, $event)
        $page = $pages[$progress.Index]
        $event.PageSettings.Landscape = $page.width -gt $page.height
    })
    $document.add_PrintPage({
        param($sender, $event)
        $page = $pages[$progress.Index]
        $image = [Drawing.Bitmap]::new((Join-Path $folder $page.file))
        try {
            $graphics = $event.Graphics
            # With OriginAtMargins disabled, (0, 0) is the printable area's top-left corner.
            $dpiX = $graphics.DpiX
            $dpiY = $graphics.DpiY
            $deviceContext = $graphics.GetHdc()
            try {
                $availableWidth = [PrinterDevice]::GetDeviceCaps(
                    $deviceContext, [PrinterDevice]::HorizontalResolution)
                $availableHeight = [PrinterDevice]::GetDeviceCaps(
                    $deviceContext, [PrinterDevice]::VerticalResolution)
                if ($availableWidth -le 0 -or $availableHeight -le 0) {
                    throw 'The printer did not report a valid printable area.'
                }
                $width = $page.width * $dpiX / 72.0
                $height = $page.height * $dpiY / 72.0
                # Leave clearance inside the reported area before handing the raster to the driver.
                $fitScale = [Math]::Min(1, [Math]::Min($availableWidth / $width, $availableHeight / $height))
                $scale = $fitScale * 0.95
                $width = [Math]::Max(1, [Math]::Floor($width * $scale))
                $height = [Math]::Max(1, [Math]::Floor($height * $scale))
                $x = [Math]::Floor(($availableWidth - $width) / 2)
                $y = [Math]::Floor(($availableHeight - $height) / 2)
                [PrinterDevice]::DrawPage($deviceContext, $image, $x, $y, $width, $height)
            }
            finally {
                $graphics.ReleaseHdc($deviceContext)
            }
        }
        finally {
            $image.Dispose()
        }
        $progress.Index++
        $event.HasMorePages = $progress.Index -le $progress.Last
    })
    $document.Print()
}
finally {
    $dialog.Dispose()
    $document.Dispose()
}
