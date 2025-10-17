# What is it?
**KathaRange** is a cybersecurity network emulation system built on the [Kathará Framework API](https://github.com/KatharaFramework). It can create a dynamic, containerized network of machines for cyber attack and defense scenarios, featuring leading open-source technologies such as Snort3 IDS, Wazuh SIEM, MITRE's Caldera, and a Kali Linux machine equipped with Atomic Red Team tools.

### Requirements:
- Linux host machine
- Docker
- Python

### Getting Started
Launch the script in the root of the project:

    ./init.sh
The script will automatically clone all dependencies and build all images needed by the  lab. You can go grab a coffee, it will take some time to complete :coffee:

Once the process is completed, run the following commands to install Kathara python API:
```
python3 -m pip install git+https://github.com/saghul/pyuv@master#egg=pyuv
python3 -m pip install "kathara"
```
Then you can start a lab with:

```
python3 start_lab.py
```
You can also view all available options by running:

```
python3 start_lab.py --help
```

⚠️ Wait for all terminals to completely load the startup scripts, then you will able to access all services.

Caldera will be listening on:
http://localhost:8888/

depending on which team you want to play:

usr/psw

    red/lRGXTicDZEh_TW23gFoLLrB8uqhat_EogkJD-a2foVg
    blue/e-1yjrXMhf6lKXoKcHAl8VS7P2-aIbFymQqBvwOJ4Xc

or if you restart the server in the container with `--insecure` (edit caldera.startup and restart the container or kill the process in the machine and restart `python3 server.py --insecure`) 

    admin/admin

Wazuh will be at:
https://localhost

usr/psw

    admin/SecretPassword

