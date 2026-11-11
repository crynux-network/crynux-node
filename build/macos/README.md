## Build the Mac release package of the Crynux node

### All in one script


#### Local build for testing

```shell
# In the root folder of the project

$ ./build/macos/build.sh
```
The app package (Crynux Node.app) and the binaries inside the package will be signed using a local developer certificate
automatically.

|-- Crynux Node.dmg -> not signed

|------ Crynux Node.app -> signed by pyinstaller

|---------- Contents/MacOS/crynux -> signed by pyinstaller

|---------- Contents/Frameworks/crynux_worker_process/ -> onedir worker, signed after embed

|---------- ...all the python .so/.lib executable files... -> signed by pyinstaller

#### Production build for releasing

When building the release package that will be distributed to the public,
please use a team Apple account by providing the ID of the [Developer ID certificate](https://developer.apple.com/help/account/create-certificates/create-developer-id-certificates/)
as argument:

```shell
# In the root folder of the project

$ ./build/macos/build.sh -s [CERTIFICATE_ID] -u [APPLE USERNAME] -t [APPLE TEAM ID] -p [APPLE APP SPECIFIC PASSWORD]
```

Before executing the command above,
the certificate and its private key must be downloaded to the local keychain using Xcode.
Login the account in the Xcode settings, make sure the certificate is listed and click the download manual profiles button.

***Examine the certificate and make sure the certificate is trusted by the local device using the Keychain app***.

The DMG file, the app package (Crynux Node.app) and the binaries inside the package will all be
signed using the provided certificate. The DMG file will be sent to Apple for notarization.
If the notarization passes, the DMG file will be stapled.


|-- Crynux Node.dmg -> signed, notarized and stapled by the build.sh script

|------ Crynux Node.app -> signed by pyinstaller

|---------- Contents/MacOS/crynux -> signed by pyinstaller

|---------- Contents/Frameworks/crynux_worker_process/ -> onedir worker, signed after embed

|---------- ...all the other python .so/.lib executable files... -> signed by pyinstaller


### Step by step

1. Prepare the distribution folder for the packaging

Run the following command inside the root folder of the project:

```shell
$ ./build/macos/prepare.sh -w build/crynux_node
```

A new distribution folder will be created as ```build/crynux_node```,
all the dependencies will be fetched, venvs will be built, and sub projects such as webui will be built.

2. Packaging the application

Go to the folder created in the last step, and run the package command:

```shell
$ cd build/crynux_node
$ ./package.sh
```

```Crynux Node.app``` and ```Crynux Node.dmg``` will be created under
the ```dist``` folder of the distribution folder created in the last step.

3. Notarization

Notarization could be performed using the python script:

```shell
$ python notarize.py --package "dist/Crynux Node.dmg" --username [APPLE USERNAME] --team [APPLE TEAM ID] --password [APPLE APP SPECIFIC PASSWORD]
```

### References

[https://haim.dev/posts/2020-08-08-python-macos-app/](https://haim.dev/posts/2020-08-08-python-macos-app/)

[https://github.com/ThomasDebrunner/notarizer](https://github.com/ThomasDebrunner/notarizer)
