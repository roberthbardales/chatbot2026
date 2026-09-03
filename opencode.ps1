Add-Type @"
using System;
using System.Runtime.InteropServices;

public class ConsoleFont {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CONSOLE_FONT_INFOEX {
        public uint cbSize;
        public uint nFont;
        public short dwFontSizeX;
        public short dwFontSizeY;
        public int FontFamily;
        public int FontWeight;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
        public string FaceName;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool SetCurrentConsoleFontEx(
        IntPtr hConsoleOutput,
        bool bMaximumWindow,
        ref CONSOLE_FONT_INFOEX lpConsoleCurrentFontEx
    );
}
"@

# Obtener la consola actual
$handle = [ConsoleFont]::GetStdHandle(-11)

# Configurar Lucida Console tamaño 15
$font = New-Object ConsoleFont+CONSOLE_FONT_INFOEX
$font.cbSize = [Runtime.InteropServices.Marshal]::SizeOf($font)
$font.nFont = 0
$font.dwFontSizeX = 0
$font.dwFontSizeY = 15
$font.FontFamily = 54
$font.FontWeight = 400
$font.FaceName = "Lucida Console"

[ConsoleFont]::SetCurrentConsoleFontEx($handle, $false, [ref]$font)

# Ir a la carpeta donde está el script
Set-Location -LiteralPath $PSScriptRoot

# Ejecutar OpenCode
opencode