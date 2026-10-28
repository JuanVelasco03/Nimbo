; Instalador de Nimbo (Inno Setup 6).
;
;   1) python empaquetado\construir.py ...     -> deja dist\Nimbo\
;   2) compilar este .iss                      -> deja dist\NimboSetup.exe
;
; Instala **per-user**, en %LOCALAPPDATA%\Programs\Nimbo, y eso no es una
; preferencia: config.json, estado\, datos\ y nucleo\scripts\grabar.txt
; cuelgan de la misma raiz que el codigo (ajustes.py), asi que en
; "Program Files" no serian escribibles sin elevacion. Ver
; specs\distribucion_instalador.md S3.

#define NombreApp "Nimbo"
#define Version "1.0.0"
#define Editor "Nimbo"
#define Origen "..\dist\Nimbo"

[Setup]
AppId={{B2F4E1C7-5A9D-4F3E-8C21-6D0A7B4E9F12}
AppName={#NombreApp}
AppVersion={#Version}
AppPublisher={#Editor}
DefaultDirName={localappdata}\Programs\{#NombreApp}
DefaultGroupName={#NombreApp}
DisableProgramGroupPage=yes
; Sin admin: todo lo que toca es la carpeta del usuario.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=NimboSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\nimbo\web\icono.ico
; El paquete pasa los 190 MB (640 con el respaldo de navegador): que el
; disco tenga lugar antes de empezar a copiar.
ExtraDiskSpaceRequired=0

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"
Name: "inicio"; Description: "Arrancar Nimbo al iniciar Windows"; GroupDescription: "Opciones:"; Flags: unchecked

[Files]
; Todo lo que armo construir.py, tal cual. recursesubdirs toma runtime\
; completo (Python embebido, mitmproxy y, si se armo con --completo, los
; navegadores de Playwright).
Source: "{#Origen}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Apunta derecho al pythonw embebido: no hace falta que el usuario tenga
; Python. arrancar.bat sigue existiendo para el arranque con Windows (es
; a lo que apunta el shim de inicio_automatico.py).
Name: "{group}\{#NombreApp}"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\nimbo\app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\nimbo\web\icono.ico"
Name: "{group}\Desinstalar {#NombreApp}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#NombreApp}"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\nimbo\app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\nimbo\web\icono.ico"; Tasks: escritorio

[Run]
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\nimbo\app.py"""; WorkingDir: "{app}"; Description: "Abrir Nimbo ahora"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Lo que Nimbo genera solo y no es del usuario. Las grabaciones (datos\),
; config.json y grabar.txt NO se borran a proposito: son horas de video y
; la lista de a quien se graba. Que las borre el usuario si quiere.
Type: filesandordirs; Name: "{app}\perfil_ventana"
Type: filesandordirs; Name: "{app}\nucleo\perfil"
Type: filesandordirs; Name: "{app}\estado"
Type: filesandordirs; Name: "{app}\registro"
Type: filesandordirs; Name: "{app}\runtime"
Type: files; Name: "{app}\instancia.json"

[Code]
// El arranque con Windows reusa el mecanismo que ya tiene Nimbo
// (inicio_automatico.py: un .bat en la carpeta de Inicio que llama a
// arrancar.bat) en vez de inventar una entrada de registro propia. Asi
// el conmutador de la pestana Ajustes sigue siendo la unica fuente de
// verdad y se puede apagar desde la interfaz.
procedure CurStepChanged(CurStep: TSetupStep);
var
  Inicio, Contenido: String;
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('inicio') then
    begin
      Inicio := ExpandConstant('{userstartup}\Nimbo.bat');
      Contenido := 'rem Nimbo -- generado automaticamente, se puede borrar sin miedo' + #13#10 +
                   '@echo off' + #13#10 +
                   'start "" "' + ExpandConstant('{app}\arrancar.bat') + '"' + #13#10;
      SaveStringToFile(Inicio, Contenido, False);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    DeleteFile(ExpandConstant('{userstartup}\Nimbo.bat'));
end;
