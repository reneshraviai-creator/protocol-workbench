"""Generate transparent Windows wrappers; never changes antivirus/policy settings."""


def powershell_command():
 # All variable input comes from environment variables, not interpolated script text.
 return " ".join(line.strip() for line in r'''
$ErrorActionPreference='Stop';
try {
 $action=$env:PW_ACTION;
 if($action -notin @('install','start','stop','status','validate','diagnostics','rollback','uninstall','check-package','check-runtime')) { throw 'Unsupported installer action' };
 $root=[IO.Path]::GetFullPath($env:PW_PACKAGE_ROOT);
 Write-Output ('Protocol Workbench package action: '+$action);
 if($action -eq 'install' -and $root -match '\.zip[.\\]') { throw 'Do not install from the Windows ZIP preview temporary folder. Use Extract All into a permanent folder such as C:\PW.' };
 $setup=Join-Path $root 'setup.py';
 $python=Join-Path $root 'runtime\python.exe';
 if($action -ne 'stop' -and $action -ne 'status') {
  $manifest=Join-Path $root 'SHA256SUMS.txt';
  if(!(Test-Path -LiteralPath $manifest -PathType Leaf)) { throw 'Package manifest missing. Extract ALL files into a permanent folder, not a ZIP preview.' };
  $seen=@{};
  foreach($line in Get-Content -LiteralPath $manifest) {
   if($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw 'Invalid package manifest entry' };
   $expected=$Matches[1]; $name=$Matches[2];
   $path=[IO.Path]::GetFullPath((Join-Path $root $name));
   if([IO.Path]::IsPathRooted($name) -or !$path.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) -or $seen.ContainsKey($name)) { throw 'Invalid or duplicate package path' };
   $seen[$name]=$true;
   if(!(Test-Path -LiteralPath $path -PathType Leaf)) { throw ('Missing package file: '+$name+'. Check Windows Security Protection history or your antivirus quarantine. Do not disable protection or restore flagged files without investigation.') };
   if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected) { throw ('Package checksum mismatch: '+$name+'. Do not run this copy; obtain and verify a complete replacement.') };
  };
  foreach($required in @('setup.py','payload/app/server.py','payload/app/version.py','runtime/python.exe')) { if(!$seen.ContainsKey($required)) { throw ('Manifest does not cover required file: '+$required) } };
  Write-Output 'Package integrity checks passed.';
 };
 if($action -eq 'check-package') { exit 0 };
 if(!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Bundled Python is missing. Check extraction and antivirus quarantine; no installed project data has been deleted.' };
 if(!(Test-Path -LiteralPath $setup -PathType Leaf)) { throw 'setup.py is missing. Use the complete extracted installer.' };
 if($action -eq 'check-runtime') {
  & $python -c 'import platform,sys,asyncua,pymodbus,aiohttp,serial,cryptography,reportlab,PIL; print(platform.machine(),64 if sys.maxsize>2**32 else 32)';
 } else {
  $arguments=@($setup,$action);
  if($action -eq 'install') { $arguments+='--offline' };
  if($action -eq 'rollback') { $arguments+='--confirm-data-restore' };
  & $python @arguments;
 };
 exit $LASTEXITCODE;
} catch { Write-Output ('ERROR: '+$_.Exception.Message); exit 1 }
'''.splitlines())


def runner():
 command=powershell_command()
 return ('@echo off\nsetlocal\n'
         'set "PW_PACKAGE_ROOT=%~dp0"\nset "PW_ACTION=%~1"\nset "PYTHONDONTWRITEBYTECODE=1"\n'
         'set "PW_LOG_DIR=%LOCALAPPDATA%\\ProtocolWorkbenchInstallerLogs"\n'
         'if not defined LOCALAPPDATA set "PW_LOG_DIR=%TEMP%\\ProtocolWorkbenchInstallerLogs"\n'
         'if not exist "%PW_LOG_DIR%" mkdir "%PW_LOG_DIR%"\n'
         'set "PW_LOG=%PW_LOG_DIR%\\workbench-%RANDOM%-%RANDOM%.log"\n'
         'echo Checking package. Please wait...\n'
         '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -NonInteractive -Command "'+command+'" >"%PW_LOG%" 2>&1\n'
         'set "PW_RESULT=%ERRORLEVEL%"\n'
         'type "%PW_LOG%"\necho.\necho Log saved to: "%PW_LOG%"\n'
         'if not "%PW_RESULT%"=="0" echo Operation failed. Keep this log. Do not disable antivirus or add exclusions.\n'
         'exit /b %PW_RESULT%\n').replace('\n','\r\n')


def wrapper(action,rollback=False,always_pause=False):
 confirmation=('set /p PW_CONFIRM="Restore previous release AND saved data? Type RESTORE: "\n'
               'if not "%PW_CONFIRM%"=="RESTORE" exit /b 1\n') if rollback else ''
 return ('@echo off\nsetlocal\n'
         'if not exist "%~dp0Run Workbench.bat" (\n'
         ' echo Missing Run Workbench.bat. Extract ALL files and check antivirus quarantine.\n pause\n exit /b 1\n)\n'
         +confirmation+'call "%~dp0Run Workbench.bat" '+action+'\n'
         'set "PW_RESULT=%ERRORLEVEL%"\n'
         +('pause\n' if always_pause else 'if not "%PW_RESULT%"=="0" pause\n')+
         'exit /b %PW_RESULT%\n').replace('\n','\r\n')
