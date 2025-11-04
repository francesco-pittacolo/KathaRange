from Kathara.manager.Kathara import Kathara
from Kathara.model.Lab import Lab
from Kathara.setting import Setting
import subprocess
import threading
import argparse
import readline
import random
import string
import signal
import shlex
import time
import logs
import yaml
import sys
import os
import re


# Use the same Python interpreter as the one running this script
python_path = sys.executable  

def connect_tty_xterm(router_name, lab_name):
    """
    Open an xterm window and attach to the router's TTY for interactive use.
    """
    cmd = (
        f"{shlex.quote(python_path)} -c "
        f"\"from Kathara.manager.Kathara import Kathara; "
        f"Kathara.get_instance().connect_tty('{router_name}', lab_name='{lab_name}', logs=True)\""
    )
    # Use preexec_fn=os.setsid to run the xterm in a separate process group
    return subprocess.Popen(
        ["xterm", "-hold", "-e", "bash", "-c", cmd],
        preexec_fn=os.setsid
    )

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
            "assets": cfg.get("assets", None),
            "interfaces": cfg.get("interfaces", {}),
            "addresses": cfg.get("addresses", None),
            "options": cfg.get("options") or {},
            "spawn_terminal": cfg.get("spawn_terminal", False),
        }

    return lab_info, parsed_devices


def parse_actions(filename: str):
    """
    Parse the YAML actions file and return them as a dictionary.
    Supports:
    - Named actions
    - Numeric-only commands (wrap in random-name action)
    - Each action can be:
        - simple command as str
        - tuple/list [command, expected]
        - dict {command, expected}
    - Compound actions with operator (AND/OR)
    """
    import random, string, yaml

    with open(filename, "r") as f:
        data = yaml.safe_load(f)

    actions = data.get("actions", {})
    parsed_actions = {}

    def normalize_action(action):
        """Return (command, expected) tuple for any action."""
        if isinstance(action, str):
            return (action, None)
        elif isinstance(action, (list, tuple)):
            if len(action) != 2:
                raise ValueError(f"Action tuple/list must have 2 elements: {action}")
            return tuple(action)
        elif isinstance(action, dict):
            if "command" not in action:
                raise ValueError(f"Action dict missing 'command': {action}")
            return (action["command"], action.get("expected"))
        else:
            raise ValueError(f"Unsupported action format: {action}")

    for machine, machine_data in actions.items():
        parsed_actions[machine] = {}

        # Check if all keys are numeric → no named actions
        if all(isinstance(v, (str, dict, list)) for v in machine_data.values()) and all(
            str(k).isdigit() or (isinstance(v, dict) and "operator" in v)
            for k, v in machine_data.items()
        ):
            random_action_name = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
            machine_data = {random_action_name: machine_data}

        # Parse all actions
        for action_name, action_data in machine_data.items():
            parsed_actions[machine][action_name] = []

            for key, action_value in action_data.items():
                # Compound action (OR / AND)
                if isinstance(action_value, dict) and "operator" in action_value:
                    operator = action_value["operator"]
                    if operator not in ("AND", "OR"):
                        raise ValueError(f"Unsupported operator: {operator}")
                    sub_actions = [normalize_action(v) for k, v in sorted(action_value.items()) if k != "operator"]
                    parsed_actions[machine][action_name].append((operator, *sub_actions))
                else:
                    parsed_actions[machine][action_name].append(normalize_action(action_value))

    return parsed_actions




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


def generate_expected_routes(devices, lab_folder):
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
        conf_path = os.path.join(lab_folder, "assets", "routers", name, "etc", "zebra", "ospfd.conf")
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
        print(f"[{name}] Command failed: {stderr.decode().strip()}")
        return False

    ospf_routes = stdout.decode().strip()
    print(f"\n=== {name} OSPF Routes ===\n{ospf_routes}\n")

    # Verify that each expected prefix is present
    for prefix in expected_routes[name]:
        if prefix not in ospf_routes:
            print(f"[{name}] Missing {prefix}")
            return False

    print(f"[{name}] All expected routes present")
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

def parse_args():
    parser = argparse.ArgumentParser(description="Deploy Kathara lab.", formatter_class=argparse.RawTextHelpFormatter)

    # Gruppo per argomenti obbligatori
    required_group = parser.add_argument_group("required arguments")
    required_group.add_argument(
        "lab_name",
        nargs="?",  # <-- rende l'argomento opzionale temporaneamente
        help="Lab folder to use (e.g. 'lab', 'lab2', 'bob')\nAsked if not provided."
    )

    # Gruppo per flag opzionali
    optional_group = parser.add_argument_group("optional features")
    optional_group.add_argument(
        "--spawn-terminals",
        action="store_true",
        help="Open a terminal for each device."
    )
    optional_group.add_argument(
        "--check-ospf",
        action="store_true",
        help="Check OSPF routing tables for convergence."
    )
    args = parser.parse_args()

    # Se non viene fornito lab_name, chiedilo all'utente
    if not args.lab_name:
        while True:
            try:
                lab_input = input("Enter lab name: ").strip()
                # Controlla che il nome contenga solo lettere, numeri, underscore o dash
                if not re.fullmatch(r"[A-Za-z0-9_-]+", lab_input):
                    print("Invalid lab name! Only letters, numbers, underscores, and dashes are allowed.")
                    continue

                # Controlla che la cartella del lab esista
                lab_folder = os.path.join(script_dir, lab_input)
                lab_conf_file = os.path.join(lab_folder, "lab_conf.yaml")
                if not os.path.isfile(lab_conf_file):
                    print(f"Lab '{lab_input}' not found. Please try again. (Ctrl + C to exit)")
                    continue

                # Nome valido e lab esistente
                args.lab_name = lab_input
                break
            
            except KeyboardInterrupt:
                print("\nOperation cancelled by user.")
                sys.exit(1)
    return args


def monitor_processes(processes, stop_event):
    """Monitor terminal processes and print if they are closed, but do not stop the lab."""
    while not stop_event.is_set():
        for name, p in processes.items():
            if p and p.poll() is not None:
                processes[name] = None  # Mark as closed
        time.sleep(1)


# -------------------- COMMAND SECTION ------------------------

import traceback
import functools
import atexit


def completer(text, state):
    """
    Auto-complete commands and machine names.
    """
    options = []

    # Complete command names if it's the first word
    if not readline.get_line_buffer().strip() or len(readline.get_line_buffer().split()) == 1:
        options = [cmd for cmd in cmd_commands.keys() if cmd.startswith(text)]
    else:
        # Complete machine names for commands that take machines
        options = [m for m in lab.machines.keys() if m.startswith(text)]

    if state < len(options):
        return options[state]
    return None


def setup_command_history(lab_name: str):
    """
    Initialize readline history and tab completion for the lab.
    Returns the history file path so it can be deleted later.
    """
    history_file = os.path.expanduser(f"~/.kathara_{lab_name}_history")

    # Load previous history if it exists
    try:
        readline.read_history_file(history_file)
    except FileNotFoundError:
        pass

    # Save history at exit
    atexit.register(readline.write_history_file, history_file)

    # Set tab completion
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")

    return history_file


def handle_errors(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            print("\nOperation interrupted by user (Ctrl+C).")
        except Exception as e:
            print(f"\nUnexpected error in {func.__name__}: {e}")
    return wrapper

@handle_errors
def cmd_exit(args = None):
    """
    Stop the lab and close all terminals
    """
    stop_event.set()

    for name, p in processes.items():
        if p and p.poll() is None:
            p.terminate()

    # Undeploy lab
    try:
        print("Stopping and removing lab...")
        Kathara.get_instance().undeploy_lab(lab_name=lab.name)
        print("Lab stopped and removed.")
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"Failed to undeploy lab: {e}")
    try:
        if os.path.isfile(HISTORY_FILE):
            os.remove(HISTORY_FILE)
    except:
        pass

@handle_errors
def cmd_help(args=None):
    """
    Show available commands and their descriptions.
    Usage: help
    Usage: help [command]
    """
    if args:
        # Mostra docstring completa del comando specifico
        cmd_name = args[0].lower()
        cmd_func = cmd_commands.get(cmd_name)
        if cmd_func:
            doc = cmd_func.__doc__ or "No description available."
            print(doc.strip())  # stampiamo tutta la docstring
        else:
            print(f"No such command: {cmd_name}")
    else:
        # Mostra tutte le commandi con il nome e docstring completa
        print("\nAvailable commands:\n")
        for name, func in cmd_commands.items():
            doc = func.__doc__ or "No description available."
            print(f"{name}:\n{doc.strip()}\n")
    
def get_stats(machine):
    stats_gen = Kathara.get_instance().get_machine_stats(machine, lab=lab)
    stats = next(stats_gen, None)
    if stats:
        print(stats)
    else:
        print(f"{machine}: Not running")

@handle_errors
def cmd_status(args):
    """
    Show the status of specific machine or the status of all machines
    Usage status <machine1> <machine2> ...
    Example status pc1 r1
    """
    if not args:
        print("You must specify at least one machine name.")
        return
    
    #Case -a
    if len(args) == 1 and args[0] == "-a":
        args = list(lab.machines.keys())
    
    #Specific machines case
    for i in args:
        try:
            get_stats(i)
        except KeyboardInterrupt:
            raise    
        except:
            print(f"{name}: Status not found")
   

def cmd_terminal(args):
    """
    Spawn terminal for specific machines (1+)
    Usage: terminal <machine1> <machine2> ...
    Example: terminal pc1 r2

    Use flag -a to spawn terminal for all machines
    Usage terminal -a
    """
    if not args:
        print("You must specify at least one machine name.")
        return

    spawned = []

    if len(args) == 1 and args[0] == "-a":
        args = list(lab.machines.keys())

    try:
        for name in args:
            # Skip machines not running or not in lab
            stats_gen = Kathara.get_instance().get_machine_stats(name, lab=lab)
            stats = next(stats_gen, None)
            if stats is None or name not in lab.machines:
                print(f"{name}: Machine not running or not found.")
                continue

            # Skip if terminal already exists
            p = processes.get(name)
            if p and p.poll() is None:
                print(f"Terminal for {name} is already running.")
                continue

            # Spawn terminal
            p = connect_tty_xterm(name, lab_name)
            processes[name] = p
            spawned.append(name)

    except KeyboardInterrupt:
        print("\nTerminal spawning interrupted by user. Already spawned terminals remain open.")

    if spawned:
        print(f"Spawned terminals for: {', '.join(spawned)}")

@handle_errors
def cmd_undeploy(args):
    """
    Undeploy specific machines in the lab.
    Usage: undeploy <machine1> <machine2> ...
    """
    if not args:
        print("You must specify at least one machine name.")
        return

    if len(args) == 1 and args[0] == "-a":
        args = list(lab.machines.keys())
    
    undeployed = []
    for name in args:
        try:
            stats_gen = Kathara.get_instance().get_machine_stats(name, lab=lab)
            stats = next(stats_gen, None)
            
            if stats is None:
                print(f"{name} is already stopped.")
                continue

            Kathara.get_instance().undeploy_lab(lab=lab, selected_machines=[name])
            # terminate terminal if it exists
            p = processes.get(name)
            if p and p.poll() is None:
                p.terminate()
                print(f"Terminal for {name} closed.")
            undeployed.append(name)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"Error: Failed to undeploy machine {name}: {e}")

    if undeployed:
        print(f"Machines undeployed: {', '.join(undeployed)}")
    else:
        print("No machines were undeployed")

@handle_errors
def cmd_deploy(args):
    """
    Deploy specific machines in the lab.
    Usage: deploy <machine1> <machine2> ...
    """
    if not args:
        print("You must specify at least one machine name.")
        return
    
    if len(args) == 1 and args[0] == "-a":
        args = list(lab.machines.keys())

    deployed = []
    for name in args:
        try:
            stats_gen = Kathara.get_instance().get_machine_stats(name, lab=lab)
            stats = next(stats_gen, None)
            
            if stats is not None:
                print(f"{name} is already running.")
                continue

            Kathara.get_instance().deploy_lab(lab=lab, selected_machines=[name])
            # spawn terminal if needed
            dev = devices.get(name)
            if dev and (spawn_terminals or dev.get("spawn_terminal", False)):
                p = connect_tty_xterm(name, lab_name)
                processes[name] = p
            deployed.append(name)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"Error: Failed to deploy machine {name}: {e}")

    if deployed:
        print(f"Machines deployed: {', '.join(deployed)}")
    else:
        print("No machines were deployed")

@handle_errors
def cmd_restart(args):
    """
    Restart machines in the lab.
    Usage: restart <machine1> <machine2>
    Example: restart pc1 caldera
    """
    if not args:
        print("You must specify at least one machine name.")
        return
    if len(args) == 1 and args[0] == "-a":
        print("\nRestarting all machines:")
        args = list(lab.machines.keys())
    #print(args)  
    cmd_undeploy(args)
    cmd_deploy(args)


def exec_command(machine_name, command):
    try:
        stdout, stderr, code = Kathara.get_instance().exec(
                                    machine_name=machine_name,
                                    command=command,
                                    lab=lab,
                                    stream=False
                            )
        return stdout, stderr, code
    except Exception as e:
        print(f"Exception while executing action on {machine_name}: {e}\n")
        return None , str(e), 1  

@handle_errors
def cmd_action(args):
    """
    Execute actions for machines defined in the file actions.yaml.
    Usage: action <machine1> <action1> <action2> <machine2> <action1>
    Examples:
      action kali test          -> runs 'test' on kali
      action kali -a            -> runs all actions on kali
      action r1                 -> runs all commands of r1 (if single action declared)
      action kali test r1       -> runs 'test' and all commands of r1
      action kali -a r2 testr2  -> runs all actions of kali and 'testr2' on r2
      action -a                 -> runs all actions on all machines
    """
    if not args:
        print("You must specify at least one machine name.")
        return

    # Global flag: execute all actions of all machines
    if len(args) == 1 and args[0] == "-a":
        targets = {m: list(actions[m].keys()) for m in actions}
    else:
        targets = {}
        current_machine = None

        for token in args:
            if token in actions:
                # Found a new machine
                current_machine = token
                targets[current_machine] = []
            elif token == "-a" and current_machine:
                # Run all actions of current machine
                targets[current_machine] = list(actions[current_machine].keys())
            elif current_machine:
                # Action name for current machine
                targets[current_machine].append(token)
            else:
                print(f"Ignoring '{token}': action not found in machine {machine}.")

    # Handle default actions for machines without specified actions
    for machine, acts in list(targets.items()):
        if not acts:
            machine_actions = actions[machine]
            action_names = list(machine_actions.keys())

            # If only one action, execute it
            if len(action_names) == 1:
                targets[machine] = action_names
            else:
                # Multiple named actions
                print(f"Multiple possible actions found for machine '{machine}': {', '.join(action_names)}. Specify one or use -a to execute all.")
                targets[machine] = []

    # Execute actions per machine
    for machine, action_list in targets.items():
        if not action_list:
            continue  # skip machines with no actions to run

        machine_actions = actions[machine]

        for action_name in action_list:
            if action_name not in machine_actions:
                print(f"Action '{action_name}' not found for machine '{machine}'. Skipping.")
                continue

            commands_log = {}  # Will collect all executed commands for this action
            commands = machine_actions[action_name]
            print(f"\n=== Actions to do on {machine} [{action_name}] ===\n")

            for i, command in enumerate(commands, 1):
                if isinstance(command, tuple) and isinstance(command[0], str) and command[0].upper() in ("AND", "OR"):
                    parent_label = i
                    commands_log[parent_label] = {}
                    # Compound action
                    operator = command[0].upper()
                    sub_actions = command[1:]
                    print(f"Running command group {i}/{len(commands)} ({operator}): {len(sub_actions)} sub-commands\n")
                    success = (operator == "AND")

                    for sub_label, sub_step in enumerate(sub_actions, 1):
                        cmd, expected = sub_step
                        label = f"{i}{chr(96 + sub_label)}"
                        try:
                            print(f"Executing sub-command {label}: {cmd} (expected: {expected})\n")
                            stdout, stderr, code = exec_command(machine, cmd)
                            output = stdout.decode().strip() if stdout else ""

                            # Append to commands_log
                            commands_log[parent_label][label] = {
                                "command": cmd,
                                "expected": expected,
                                "output": output,
                                "return_code": code
                            }

                            if code == 0:
                                print(output)
                                if expected is not None and expected not in output and operator == "AND":
                                    success = False
                                    break
                                if operator == "OR" and expected in output:
                                    success = True
                                    break
                            else:
                                print(f"Error in machine {machine} for sub-command {label}, see logs for more")
                                if operator == "AND":
                                    success = False
                                    break

                        except Exception as e:
                            print(f"Fatal error in machine {machine} for sub-command {label}: {e}")

                    if not success:
                        print(f"Command group {i} ({operator}) failed on {machine}\n")
                        break
                    else:
                        print(f"Command group {i} ({operator}) completed successfully on {machine}\n")

                else:
                    # Simple command
                    cmd, expected = command
                    label = i
                    try:
                        print(f"Running command {i}/{len(commands)}: {cmd} (expected: {expected})\n")
                        stdout, stderr, code = exec_command(machine, cmd)
                        output = stdout.decode().strip() if stdout else stderr.decode().strip()

                        # Append to commands_log
                        commands_log[label]= {
                            "command": cmd,
                            "expected": expected,
                            "output": output,
                            "return_code": code
                        }

                        if code != 0:
                            print(f"Error in machine {machine} for command {i}: {stderr}\n")
                            break
                        if expected is not None and expected not in output:
                            print(f"Expected output not found for command {i}: {expected}\n")
                            break
                        #print(output)

                    except Exception as e:
                        print(f"Fatal error in machine {machine} for command {i}: {e}\n")
            #print(commands_log)
            # Save the entire action log at the end
            logs.save_action_log_yaml(
                lab_path=lab_folder,
                machine=machine,
                action_name=action_name,
                commands=commands_log
            )


cmd_commands = {
    "exit": cmd_exit,
    "help": cmd_help,
    "status": cmd_status,
    "terminal": cmd_terminal,
    "deploy": cmd_deploy,
    "undeploy": cmd_undeploy,
    "restart": cmd_restart,
    "action": cmd_action
}

# ----------------------------------------------

if __name__ == "__main__":
    try:

        script_dir = os.path.dirname(os.path.abspath(__file__))
        args = parse_args()

        lab_name_arg = args.lab_name
        spawn_terminals = args.spawn_terminals
        check_r_ospf = args.check_ospf
        
        HISTORY_FILE = setup_command_history(lab_name_arg)

        lab_folder = os.path.join(script_dir, lab_name_arg)
        #print(lab_folder)

        # Load lab configuration
        lab_info, devices = load_lab(os.path.join(lab_folder, "lab_conf.yaml"))
        lab_name = lab_info.get("description")
        if os.path.isfile(os.path.join(lab_folder,"actions.yaml")): 
            actions = parse_actions(os.path.join(lab_folder,"actions.yaml"))
        # Generate dynamic expected_routes
        expected_routes = generate_expected_routes(devices, lab_folder)
        #print("Dynamic expected_routes:", expected_routes) # for debug

        Kathara.get_instance().undeploy_lab(lab_name=lab_name)

        # Initialize lab
        print(f"Creating Lab {lab_name}...")
        lab = Lab(lab_name)

        lab_devices = {}

        # Create devices from configuration
        for name, dev in devices.items():

            #print(f"\nCreating device '{name}'")
            #print(f"  Image: {dev['image']}")
            #print(f"  Interfaces: {dev['interfaces'] if dev['interfaces'] else 'None'}")
            #print(f"  Options: {dev['options'] if dev['options'] else 'None'}\n")

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
            if dev["assets"] == None:
                machine_folder_name = os.path.join(lab_folder, "assets", name)
                if os.path.isdir(machine_folder_name):
                    device.copy_directory_from_path(machine_folder_name, f"/")
        
                # Copy router-specific assets if available
                router_folder_name = os.path.join(lab_folder, "assets", "routers", name)
                if os.path.isdir(router_folder_name):
                    device.copy_directory_from_path(router_folder_name, f"/")
            else:
                # If custom assets are defined as a list in the YAML
                try:
                    for asset_path in dev["assets"]:
                        abs_asset_path = os.path.abspath(asset_path)
                        if os.path.exists(abs_asset_path):
                            if os.path.isdir(abs_asset_path):
                                # Copy entire directory
                                dest_path = "/"
                                device.copy_directory_from_path(abs_asset_path, dest_path)
                            else:
                                # Copy single file (e.g., README.md)
                                dest_path = f"/{asset_path}"
                                device.create_file_from_path(abs_asset_path, dest_path)
                        else:
                            print(f"Asset not found: {abs_asset_path}")
                except Exception as e:
                    print(f"Failed to copy custom assets for {name}: {e}")

            # Handle startup files
            startup_file = os.path.join(lab_folder, "startups", f"{name}.startup")
            prepare_startup_file(startup_file, name, dev, lab)

            # Copy agent/snort dependencies if required
            if os.path.isfile(startup_file):
                with open(startup_file, "r") as sf:
                    content = sf.read()
                    if "init_caldera" in content:
                        try:
                            device.copy_directory_from_path(os.path.join(lab_folder, "assets", "agents"), "/agents")
                        except:
                            print("Directory agents not found")
                            continue
                    #Management of this part to be reviewed
                    lab_has_wazuh = any(map(lambda d: "wazuh" in d["image"].lower(), devices.values()))

                    if "wazuh" in content or ("snort" in dev["image"].lower() and lab_has_wazuh):
                        try:
                            device.create_file_from_path(
                                os.path.join(lab_folder, "assets", "wazuh-agent_4.9.0-1_amd64.deb"),
                                "/wazuh-agent_4.9.0-1_amd64.deb"
                            )
                        except:
                            print("file wazuh-agent_4.9.0-1_amd64.deb not found")
                            continue
                    if "snort" in dev["image"]:
                        snort_path = os.path.join(lab_folder, "assets", "snort3")
                        if os.path.isdir(snort_path):
                            device.copy_directory_from_path(snort_path, "/snort3/")

        # Identify routers
        routers = set(map(lambda x: x.name, filter(lambda x: x.meta["type"] == "router", lab.machines.values())))

        # OSPF deployment and convergence check
        if check_r_ospf:
            Kathara.get_instance().deploy_lab(lab, selected_machines=routers)

            print("\nWaiting for OSPF convergence...")
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
                print("\nOSPF convergence achieved!")
            else:
                print("\nTimeout reached, OSPF did not fully converge.")

            Kathara.get_instance().deploy_lab(lab, excluded_machines=routers)
        else:
            Kathara.get_instance().deploy_lab(lab)

        # Open terminals
        processes = {}

        for name, dev in devices.items():
            if spawn_terminals or dev.get("spawn_terminal", False):
                p = connect_tty_xterm(name, lab_name)
                processes[name] = p
        stop_event = threading.Event()

        if processes:
            print("\nLab deployed. Type 'exit' to stop lab or 'help' to see available commands.")
            threading.Thread(target=monitor_processes, args=(processes, stop_event), daemon=True).start()
        else:
            print("\nLab deployed in background. Type 'exit' to stop.")

        try:
            while not stop_event.is_set():
                line = input("> ").strip()
                if not line:
                    continue
                parts = line.split()
                cmd_name, args = parts[0].lower(), parts[1:]
                cmd_func = cmd_commands.get(cmd_name)
                if cmd_func:
                    cmd_func(args)
                else:
                    print(f"Unknown command: {cmd_name}")

        except KeyboardInterrupt:
            print("\nLab interrupted by user.")
            cmd_exit()


    except Exception as e:
        if "Permission denied" in str(e) or "DockerDaemonConnectionError" in str(type(e)):
            print("You need root permissions to run this script (or add your user to the 'docker' group).")
            sys.exit(1)
        else:
            raise    