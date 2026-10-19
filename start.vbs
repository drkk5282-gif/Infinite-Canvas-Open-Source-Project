' QingZi Canvas — silent start (no CMD window)
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = dir

pythonw = "pythonw"
On Error Resume Next
test = shell.Run("pythonw -V", 0, True)
If Err.Number <> 0 Then
  Err.Clear
  pythonw = "python"
End If
On Error GoTo 0

shell.Run """" & pythonw & """ """ & dir & "\run.py""", 0, False
