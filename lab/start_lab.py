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


if __name__ == "__main__":
    # Always resolve paths relative to script location
    script_dir = os.path.dirname(os.path.abspath(__file__))

    #logger = logging.getLogger("Kathara")
    #logger.setLevel(logging.INFO)

    lab_info, devices = load_lab(os.path.join(script_dir, "lab_conf.yaml"))

    lab_name = lab_info.get("description")

    print(f"Creating Lab {lab_name}...")
    lab = Lab(lab_name)
    Kathara.get_instance().wipe(True)
    lab_devices = {}

    for name, dev in devices.items():
        # Create the machine
        print(f"\nCreating device '{name}'")
        print(f"  Image: {dev['image']}")
        print(f"  Interfaces: {dev['interfaces'] if dev['interfaces'] else 'None'}")
        print(f"  Options: {dev['options'] if dev['options'] else 'None'}\n")

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

        device = lab_devices[name]

        # Copy machine-specific assets if present
        machine_folder_name = os.path.join(script_dir, "assets", name)
        if os.path.isdir(machine_folder_name):
            device.copy_directory_from_path(machine_folder_name, f"/{name}/")

        # Copy router-specific assets if present
        router_folder_name = os.path.join(script_dir, "assets", "routers", name)
        if os.path.isdir(router_folder_name):
            device.copy_directory_from_path(router_folder_name, "/")

        # Startup files copy
        startup_file = os.path.join(script_dir, "startups", f"{name}.startup")
        if os.path.isfile(startup_file):
            lab.create_file_from_path(startup_file, f"{name}.startup")

            with open(startup_file, "r") as sf:
                content = sf.read()
                if "init_caldera" in content:
                    device.copy_directory_from_path(os.path.join(script_dir, "assets", "agents"), "/agents")
                if "wazuh-agent" in content or "snort" in dev["image"]:
                    device.create_file_from_path(
                        os.path.join(script_dir, "assets", "wazuh-agent_4.9.0-1_amd64.deb"),
                        "/wazuh-agent_4.9.0-1_amd64.deb"
                    )

        # Snort-specific assets
        if "snort" in dev["image"]:
            snort_path = os.path.join(script_dir, "assets", "snort3")
            if os.path.isdir(snort_path):
                device.copy_directory_from_path(snort_path, "/snort3/")

    Kathara.get_instance().deploy_lab(lab)

    # Option for spawning all terminals
    if spawn_terminals:
        processes = []
        for name in devices.keys():
            p = connect_tty_xterm(name, lab_name)
            processes.append(p)

        for p in processes:
            p.wait()

    # Stop and undeploy lab when done
    print("Stopping and removing lab...")
    Kathara.get_instance().undeploy_lab(lab_name=lab.name)
