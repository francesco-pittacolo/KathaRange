from Kathara.manager.Kathara import Kathara
from Kathara.model.Lab import Lab
import logging
import yaml
import subprocess
import shlex
import sys
import os
from Kathara.setting import Setting
python_path = sys.executable  # Automatically uses the current Python (venv or system)
spawn_terminals = True

def connect_tty_xterm(router_name, lab_name):
    cmd = (
        f"{shlex.quote(python_path)} -c "
        f"\"from Kathara.manager.Kathara import Kathara; "
        f"Kathara.get_instance().connect_tty('{router_name}', lab_name='{lab_name}', logs=True)\""
    )
    return subprocess.Popen(["xterm", "-hold", "-e", "bash", "-c", cmd])


def load_lab(filename: str):
    with open(filename, "r") as f:
        data = yaml.safe_load(f)

    lab_info = data.get("lab", {})
    devices = data.get("devices", {})

    parsed_devices = {}
    for name, cfg in devices.items():
        parsed_devices[name] = {
            "image": cfg.get("image", None),
            "interfaces": cfg.get("interfaces", {}),
            "options": cfg.get("options") or {},
        }

    return lab_info, parsed_devices
import time
if __name__ == "__main__":
    lab_info, devices = load_lab("lab_conf.yaml")

    lab_name = lab_info.get("description")
    logger = logging.getLogger("Kathara")
    logger.setLevel(logging.INFO)
    logger.info(f"Creating Lab {lab_name}...")
    lab = Lab(lab_name)
    Kathara.get_instance().wipe(True)
    lab_devices = {}
    for name, dev in devices.items():
        # Create the machine
        print()
        print(f"Creating device '{name}'")
        print(f"  Image: {dev['image']}")
        print(f"  Interfaces: {dev['interfaces'] if dev['interfaces'] else 'None'}")
        print(f"  Options: {dev['options'] if dev['options'] else 'None'}")

        lab_devices[name] = lab.new_machine(
            name,
            **{
                "image": dev["image"],
                **dev["options"],
            }
        )
        
        
        # Connect interfaces
        for iface_name, link_name in dev.get("interfaces", {}).items():
            iface_index = int(iface_name.replace("eth", ""))
            lab.connect_machine_to_link(name, link_name, machine_iface_number=iface_index)
            
        Kathara.get_instance().check_image(dev['image'])
        print()
        
        machine_folder_name = os.path.join(os.getcwd(), f"assets/{name}/")
        device = lab_devices[name]
        
        if os.path.isdir(machine_folder_name): 
            device.copy_directory_from_path(machine_folder_name,  f"/{name}/")

        router_folder_name = os.path.join(os.getcwd(), f"assets/routers/{name}/")
        if os.path.isdir(f"assets/routers/{name}"):
            device.copy_directory_from_path(router_folder_name,  f"/")
            
        lab.create_file_from_path(f"startups/{name}.startup",f"{name}.startup")

        with open(f"startups/{name}.startup", "r") as sf:
            content = sf.read()
            if "init_caldera" in content:
                device.copy_directory_from_path("assets/agents","/agents")
            if "wazuh-agent" in content or "snort" in dev["image"]:
                device.create_file_from_path(f"assets/wazuh-agent_4.9.0-1_amd64.deb", "/wazuh-agent_4.9.0-1_amd64.deb")
        print(dev["image"])
        if "snort" in dev["image"]:
            device.copy_directory_from_path("assets/snort3/","/snort3/")

    Kathara.get_instance().deploy_lab(lab)


    
    # Option for spawning all terminals
    if spawn_terminals:
        processes = []
        for name in devices.keys():
            p = connect_tty_xterm(name, lab_name)
            processes.append(p)

        for p in processes:
            p.wait()



    
    #Kathara.get_instance().wipe(True)
    Kathara.get_instance().undeploy_lab(lab_name=lab.name)
