from Kathara.manager.Kathara import Kathara
from Kathara.model.Lab import Lab
from Kathara.setting import Setting
import yaml
import subprocess
import shlex
import sys
import os
import time
import re

# Use the same Python interpreter as the one running this script
python_path = sys.executable  

# Whether to open xterm terminals for each device
spawn_terminals = True  

# Whether to check OSPF routing tables for convergence
check_r_ospf = True  

def connect_tty_xterm(router_name, lab_name):
    """
    Open an xterm window and attach to the router's TTY for interactive use.
    """
    cmd = (
        f"{shlex.quote(python_path)} -c "
        f"\"from Kathara.manager.Kathara import Kathara; "
        f"Kathara.get_instance().connect_tty('{router_name}', lab_name='{lab_name}', logs=True)\""
    )
    return subprocess.Popen(["xterm", "-hold", "-e", "bash", "-c", cmd])


def load_lab(filename: str):
    """
    Parse the YAML lab configuration file and return lab info + devices.
    """
    with open(filename, "r") as f:
        data = yaml.safe_load(f)

    lab_info = data.get("lab", {})
    devices = data.get("devices", {})

    # Normalize devices structure into a dictionary
    parsed_devices = {}
    for name, cfg in devices.items():
        parsed_devices[name] = {
            "image": cfg.get("image", None),
            "type": cfg.get("type", None),
            "interfaces": cfg.get("interfaces", {}),
            "addresses": cfg.get("addresses", None),
            "options": cfg.get("options") or {},
        }

    return lab_info, parsed_devices


def parse_ospfd_conf(conf_file):
    """
    Parse ospfd.conf and return:
    - networks: list of networks advertised in OSPF
    - is_stub: True if the router has a stub area
    - has_default_originate: True if 'default-information originate' is present
    """
    networks = []
    is_stub = False
    has_default_originate = False

    if not os.path.isfile(conf_file):
        return networks, is_stub, has_default_originate

    with open(conf_file, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("network"):
                match = re.match(r'network\s+([\d\.]+/\d+)\s+area\s+[\d\.]+', line)
                if match:
                    networks.append(match.group(1))
            elif re.match(r'area\s+[\d\.]+\s+stub', line):
                is_stub = True
            elif "default-information originate" in line:
                has_default_originate = True

    return networks, is_stub, has_default_originate


def generate_expected_routes(devices, script_dir):
    """
    Generate expected_routes dynamically from ospfd.conf files.
    - Stub routers (area X.X.X.X stub) -> only default route
    - Backbone routers -> all stub networks
    """
    stub_networks = set()
    router_confs = {}

    # First pass: parse all routers, collect stub networks
    for name, dev in devices.items():
        if dev.get("type") != "router":
            continue
        conf_path = os.path.join(script_dir, "assets", "routers", name, "etc", "zebra", "ospfd.conf")
        networks, is_stub, has_default = parse_ospfd_conf(conf_path)
        router_confs[name] = (networks, is_stub, has_default)
        if is_stub:
            stub_networks.update(networks)

    # Second pass: build expected_routes
    expected_routes = {}
    for name, (networks, is_stub, has_default) in router_confs.items():
        if is_stub:
            # Stub routers only check default route if advertised
            expected_routes[name] = ["0.0.0.0/0"] if has_default else []
        else:
            # Backbone/core routers check all stub networks
            expected_routes[name] = list(stub_networks)

    return expected_routes


def check_ospf(router, lab, expected_routes):
    """
    Verify that a router has learned all expected OSPF routes.
    Returns True if successful, False otherwise.
    """
    name = router.name
    if name not in expected_routes:
        return True  # No OSPF expectations defined for this router

    # Run OSPF route check inside the container
    stdout, stderr, rc = Kathara.get_instance().exec(
        machine_name=name,
        command=["vtysh", "-c", "show ip route ospf"],
        lab=lab,
        stream=False  # wait for command to finish
    )

    if rc != 0:
        print(f"[{name}] ⚠️ command failed: {stderr.decode().strip()}")
        return False

    ospf_routes = stdout.decode().strip()
    print(f"\n=== {name} OSPF Routes ===\n{ospf_routes}\n")

    # Verify that each expected prefix is present
    for prefix in expected_routes[name]:
        if prefix not in ospf_routes:
            print(f"[{name}] ❌ Missing {prefix}")
            return False

    print(f"[{name}] ✅ All expected routes present")
    return True


def prepare_startup_file(startup_file, name, dev, lab):
    """
    Create or update a startup file for a device.
    """
    addresses = dev.get("addresses")

    if addresses:
        existing_lines = []
        if os.path.isfile(startup_file):
            with open(startup_file, "r", encoding="utf-8") as f:
                existing_lines = f.read().splitlines()

        address_lines = [
            f"ip address add {addr} dev {iface}"
            for iface, addr in addresses.items()
            if f"ip address add {addr} dev {iface}" not in existing_lines
        ]

        final_lines = address_lines + existing_lines
        lab.create_file_from_list(final_lines, f"{name}.startup")
    else:
        if os.path.isfile(startup_file):
            lab.create_file_from_path(startup_file, f"{name}.startup")
        else:
            print(f"No addresses and no existing startup file for {name}")


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Load lab configuration
    lab_info, devices = load_lab(os.path.join(script_dir, "lab_conf.yaml"))
    lab_name = lab_info.get("description")

    
    # Generate dynamic expected_routes
    expected_routes = generate_expected_routes(devices, script_dir)
    print("Dynamic expected_routes:", expected_routes)

    Kathara.get_instance().undeploy_lab(lab_name=lab_name)

    # Initialize lab
    print(f"Creating Lab {lab_name}...")
    lab = Lab(lab_name)

    lab_devices = {}

    # Create devices from configuration
    for name, dev in devices.items():
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
        lab_devices[name].add_meta("type", dev["type"])

        # Connect interfaces
        for iface_name, link_name in dev.get("interfaces", {}).items():
            iface_index = int(iface_name.replace("eth", ""))
            lab.connect_machine_to_link(name, link_name, machine_iface_number=iface_index)

        # Ensure the image is available
        Kathara.get_instance().check_image(dev['image'])
        device = lab_devices[name]

        # Copy device-specific assets if available
        machine_folder_name = os.path.join(script_dir, "assets", name)
        if os.path.isdir(machine_folder_name):
            device.copy_directory_from_path(machine_folder_name, f"/{name}/")

        # Copy router-specific assets if available
        router_folder_name = os.path.join(script_dir, "assets", "routers", name)
        if os.path.isdir(router_folder_name):
            device.copy_directory_from_path(router_folder_name, "/")

        # Handle startup files
        startup_file = os.path.join(script_dir, "startups", f"{name}.startup")
        prepare_startup_file(startup_file, name, dev, lab)

        # Copy agent/snort dependencies if required
        if os.path.isfile(startup_file):
            with open(startup_file, "r") as sf:
                content = sf.read()
                if "init_caldera" in content:
                    device.copy_directory_from_path(os.path.join(script_dir, "assets", "agents"), "/agents")
                if "wazuh-agent" in content or "snort" in dev["image"]:
                    device.create_file_from_path(
                        os.path.join(script_dir, "assets", "wazuh-agent_4.9.0-1_amd64.deb"),
                        "/wazuh-agent_4.9.0-1_amd64.deb"
                    )
                if "snort" in dev["image"]:
                    snort_path = os.path.join(script_dir, "assets", "snort3")
                    if os.path.isdir(snort_path):
                        device.copy_directory_from_path(snort_path, "/snort3/")

    # Identify routers
    routers = set(map(lambda x: x.name, filter(lambda x: x.meta["type"] == "router", lab.machines.values())))

    # OSPF deployment and convergence check
    if check_r_ospf:
        Kathara.get_instance().deploy_lab(lab, selected_machines=routers)

        print("\n⏳ Waiting for OSPF convergence...")
        converged = False
        timeout = 180
        start_time = time.time()

        while not converged and (time.time() - start_time < timeout):
            checks = []
            for name in routers:
                r = lab.get_machine(name)
                checks.append(check_ospf(r, lab, expected_routes))
            if all(checks):
                converged = True
            else:
                time.sleep(5)

        if converged:
            print("\n✅ OSPF convergence achieved!")
        else:
            print("\n⚠️ Timeout reached, OSPF did not fully converge.")

        Kathara.get_instance().deploy_lab(lab, excluded_machines=routers)
    else:
        Kathara.get_instance().deploy_lab(lab)

    # Open terminals
    if spawn_terminals:
        processes = []
        for name in devices.keys():
            p = connect_tty_xterm(name, lab_name)
            processes.append(p)
        for p in processes:
            p.wait()

    print("Stopping and removing lab...")
    Kathara.get_instance().undeploy_lab(lab_name=lab.name)
