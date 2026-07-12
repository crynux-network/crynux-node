**NOTE: If you don't need to change the code, please use the prebuilt packages to start the node:**

[Start a node on Windows](https://docs.crynux.io/node-hosting/start-a-node-windows)

[Start a node on Mac](https://docs.crynux.io/node-hosting/start-a-node-mac)

[Start a node using Docker](https://docs.crynux.io/node-hosting/start-a-node-docker)


# Crynux Node

[![](https://dcbadge.limes.pink/api/server/https://discord.gg/zmU9GRwU6f)](https://discord.gg/zmU9GRwU6f)
[![X](https://img.shields.io/badge/@crynuxio-%23000000.svg?style=for-the-badge&logo=X&logoColor=white)](https://x.com/crynuxio)
[![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/crynux-ai/crynux-node/release-all.yml?style=for-the-badge&logo=githubactions)](https://github.com/crynux-network/crynux-node/releases)
[![Static Badge](https://img.shields.io/badge/Gitbook-Documentation-blue?style=for-the-badge&logo=gitbook)](https://docs.crynux.io)

Start a Crynux Node to share the spared local GPU to others in exchange for tokens. Crynux Node will join the Crynux Network, receive the AI inference/training/fine-tuning tasks from the network, and execute them locally.

![Crynux Node WebUI](./docs/webui.png)

## Start the node from source code

**SECURITY: DO NOT use the Web UI to set the private key if you're accessing the Web UI from a remote machine!**


If you're using HTTP protocol to access the WebUI, the connection is not encrypted. The private key might be intercepted by malicious middle man
if transferred through the HTTP connection.


Instead, set the private key in the config file directly. Or secure the connection using HTTPS.


### Prerequisite

Make sure you have the following tools installed and accessible in the path

* Python 3.10 (you might need the dev package such as python3.10-dev on Ubuntu)
* Git
* Golang 1.21
* Nodejs
* Yarn
* C Compiler (gcc is usually installed by default on Linux. on Windows, you should install a C compiler manually, such as [MinGW](https://www.mingw-w64.org/) and [TDM-GCC](https://jmeubank.github.io/tdm-gcc/))

### Clone the project

```shell
# git clone --recurse-submodules https://github.com/crynux-network/crynux-node.git
```

Or if you are pulling the latest updates from a repo cloned earlier, use the following command:

```shell
# git pull
# git submodule update --remote --merge
```

### Go to the root folder of the project

```shell
# cd crynux-node
```

### Prepare the config file

Copy ```config/config.yml.shell_example``` to ```config/config.yml```. And adjust the file content according to your need:
```shell
# In the root folder of the project
$ cp config/config.yml.shell_example config/config.yml
```

Optional process environment variables can be placed in `config/.env`. Variables
with the `SERVER_` prefix are applied to the server process at startup with the
prefix removed. Variables with the `WORKER_` prefix are applied to the worker
process each time it starts with the prefix removed, so worker-specific changes
take effect on the next worker restart. Other variables in this file are ignored.

### Prepare the server venv

1. Create the venv in the root folder of the project:

```shell
# In the root folder of the project
$ python -m venv venv
```

2. Activate the venv and install the requirements:

```shell
# In the root folder of the project

# Use ./venv/Scripts/Activate.ps1 on Windows
$ ./venv/bin/activate

# Use requirements_docker.txt if you do not need the desktop GUI
(venv) $ pip install -r ./requirements_desktop.txt

# Compile the project and install it as a pip dependency. Make sure no error is reported during this step.
(venv) $ pip install .
```


### Prepare the worker venv

1. Get the source code of stable-diffusion-task and gpt-task using Git submodule:

```shell
# In the root folder of the project
$ git submodule update --init --recursive
```

2. Create a folder named "worker" under the root folder of the project, and copy ```crynux-worker/crynux_worker_process.py``` to the folder:

```shell
# In the root folder of the project

$ mkdir worker
$ cp crynux-worker/crynux_worker_process.py worker/
```
3. Create the venv under the worker folder:

```shell
# In the root folder of the project

$ cd worker
$ python -m venv venv
```

4. Activate the venv and install the requirements for the worker:

```shell
# In the worker folder

# Use ./venv/Scripts/Activate.ps1 on Windows
$ ./venv/bin/activate

# Install the stable-diffusion-task package
(venv) $ cd ../stable-diffusion-task

## Use requirements_macos.txt on Mac
(venv) $ pip install -r requirements_cuda.txt

# Compile the module and install it as a pip dependency. Make sure no error is reported during this step.
(venv) $ pip install .

# Install the gpt-task package
(venv) $ cd ../gpt-task

# Use requirements_macos.txt on Mac
(venv) $ pip install -r requirements_cuda.txt

# Compile the module and install it as a pip dependency. Make sure no error is reported during this step.
(venv) $ pip install .

# Install the crynux-worker package
(venv) $ cd ../crynux-worker

(venv) $ pip install -r requirements.txt

# Compile the module and install it as a pip dependency. Make sure no error is reported during this step.
(venv) $ pip install .

```
### Prepare the WebUI
1. Prepare the config file
```shell
# Go to the root folder of the webui
$ cd src/webui

# Create the config file from the example
$ cp src/config.example.json src/config.json
```

2. Build the WebUI distribution package

```shell
# In the root folder of the webui

# Install the dependencies
$ yarn

# Build the package
$ yarn build
```

### Start the node

#### Start the node with desktop GUI

On Windows/Mac/Linux with GUI, you could start the Crynux GUI directly.

Activate the server's venv, and start from the ```src/app/main.py``` script:

```shell
# In the root folder of the project

# Use ./venv/Scripts/Activate.ps1 on Windows
$ ./venv/bin/activate

(venv) $ python src/app/main.py
```

#### Start the node in the terminal

On servers with no GUI, you could start the Crynux server and access the Web UI from the browser.

Activate the server's venv, and start from the server module:

```shell
# In the root folder of the project

# Use ./venv/Scripts/Activate.ps1 on Windows
$ ./venv/bin/activate

# Use $env:CRYNUX_SERVER_CONFIG = $PWD.Path + '\config\config.yml' on Windows Powershell
# Use set CRYNUX_SERVER_CONFIG=%cd%\config\config.yml on Windows CMD
(venv) $ export CRYNUX_SERVER_CONFIG="${PWD}/config/config.yml"

(venv) $ python -m crynux_server.main run
```

After the server is started, you could visit [http://127.0.0.1:7412](http://127.0.0.1:7412) in the browser to control the node.

If you are in a docker environment, or visiting the node from a remote machine, remember to expose the ```7412``` port
and use the correct IP address.


## Build the Docker image from the source code

1. Clone the project

```shell
# git clone --recurse-submodules https://github.com/crynux-network/crynux-node.git
```

Or if you are pulling the latest updates from a repo cloned earlier, use the following command:

```shell
# git pull
# git submodule update --remote --merge
```

2. Go to the root folder of the project

```shell
# cd crynux-node
```

3. Build the Docker image

```shell
# docker build -t crynux-node:dev -f .\build\Dockerfile .
```

4. Start the container

```shell
# docker run -d -p 127.0.0.1:7412:7412 --gpus all crynux-node:dev
```

## Build the binary package on Mac

Please refer to the [README](https://github.com/crynux-network/crynux-node/blob/main/build/macos/README.md) for the detailed instructions on building the MacOS binary package.


## Build the binary package on Windows

Please refer to the [README](https://github.com/crynux-network/crynux-node/blob/main/build/windows/README.md) for the detailed instructions on building the Windows binary package.

## Run tests

1. Clone the project

```shell
# git clone https://github.com/crynux-network/crynux-node.git
```

2. Go to the root folder of the project

```shell
# cd crynux-node
```

3. Install the dependencies and package (preferably in a virtualenv)

```shell
# pip install -r requirements_docker.txt && pip install .[test]
```

4. Run tests

```shell
# pytest tests
```
