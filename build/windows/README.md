## Build the Windows binary version of the Crynux node

Choose the target blockchain before building. The supported values are `base` and `near`. The blockchain value must be passed to the build script so the generated artifact folder and zip file include the required `-base` or `-near` suffix.

### All in one script

```powershell
# In the root folder of the project

C:\PROJECT_ROOT> .\build\set-config-files.ps1 base
C:\PROJECT_ROOT> .\build\windows\build.ps1 -BLOCKCHAIN base
```

### Step by step

1. Set the config files for the target blockchain

Run the following command inside the root folder of the project:

```powershell
C:\PROJECT_ROOT> .\build\set-config-files.ps1 base
```

2. Prepare the project for packaging

Run the following command inside the root folder of the project:

```powershell
C:\PROJECT_ROOT> .\build\windows\prepare.ps1 build\crynux_node
```

3. Create the distribution folder using pyinstaller

Go to the folder created in the last step, and run the package command:

```powershell
C:\PROJECT_ROOT> cd build\crynux_node
C:\PROJECT_ROOT\build\crynux_node> .\package.ps1 -BLOCKCHAIN base
```
