# Read-only screenshot: copy the rect of the window titled $Title (plus padding).
# No cursor movement, no injected input, no window activation.
param(
    [string]$Title = "DeepSeekBalance",
    [string]$Out = "shot.png",
    [int]$PadX = 0,
    [int]$PadTop = 0,
    [int]$PadBottom = 0,
    [int]$RectX = 0,
    [int]$RectY = 0,
    [int]$RectW = 0,
    [int]$RectH = 0
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class ShotWin {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    public delegate bool EnumProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    public static IntPtr Found = IntPtr.Zero;
    public static string Want = "";
    public static bool Find(IntPtr h, IntPtr p) {
        if (!IsWindowVisible(h)) return true;
        StringBuilder sb = new StringBuilder(256);
        GetWindowText(h, sb, 256);
        if (sb.ToString() == Want) { Found = h; return false; }
        return true;
    }
}
"@
[void][ShotWin]::SetProcessDPIAware()
if ($RectW -gt 0 -and $RectH -gt 0) {
    $x = $RectX; $y = $RectY; $w = $RectW; $h = $RectH
} else {
    [ShotWin]::Want = $Title
    [void][ShotWin]::EnumWindows([ShotWin+EnumProc]{ param($h, $p) return [ShotWin]::Find($h, $p) }, [IntPtr]::Zero)
    $hwnd = [ShotWin]::Found
    if ($hwnd -eq [IntPtr]::Zero) { throw "window not found: $Title" }

    $rect = New-Object ShotWin+RECT
    [void][ShotWin]::GetWindowRect($hwnd, [ref]$rect)

    $x = $rect.Left - $PadX
    $y = $rect.Top - $PadTop
    $w = ($rect.Right - $rect.Left) + $PadX * 2
    $h = ($rect.Bottom - $rect.Top) + $PadTop + $PadBottom
}

$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output ("{0}  ({1}x{2} @ {3},{4})" -f $Out, $w, $h, $x, $y)
