$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('C:\Users\ravit\OneDrive\Desktop\NIGHTFALL Evo.lnk')
$Shortcut.TargetPath = 'D:\TiTech Prabha Solution\NIGHTFALL AI\NIGHTFALL AI\NIGHTFALL-AI---Lite-main\NIGHTFALL-AI---Lite-main\.venv\Scripts\pythonw.exe'
$Shortcut.Arguments = '"D:\TiTech Prabha Solution\NIGHTFALL AI\NIGHTFALL AI\NIGHTFALL-AI---Lite-main\NIGHTFALL-AI---Lite-main\main.py"'
$Shortcut.WorkingDirectory = 'D:\TiTech Prabha Solution\NIGHTFALL AI\NIGHTFALL AI\NIGHTFALL-AI---Lite-main\NIGHTFALL-AI---Lite-main'
$Shortcut.WindowStyle = 7
$Shortcut.Description = 'Launch NIGHTFALL Evo'
if ('D:\TiTech Prabha Solution\NIGHTFALL AI\NIGHTFALL AI\NIGHTFALL-AI---Lite-main\NIGHTFALL-AI---Lite-main\assets\NIGHTFALL_Lite_Logo.ico') { $Shortcut.IconLocation = 'D:\TiTech Prabha Solution\NIGHTFALL AI\NIGHTFALL AI\NIGHTFALL-AI---Lite-main\NIGHTFALL-AI---Lite-main\assets\NIGHTFALL_Lite_Logo.ico,0' }
$Shortcut.Save()