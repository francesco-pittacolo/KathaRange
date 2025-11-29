from Kathara.manager.Kathara import Kathara
from src.action_logger import ActionLogger 
from Kathara.setting import Setting
from Kathara.model.Lab import Lab
import subprocess
import threading
import argparse
import readline
import random
import string
import signal
import shlex
import time
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
    Parse YAML actions file.
    - Each action can be:
        - str (simple command)
        - list/tuple [command, expected]
        - dict {command, expected}
    - Supports compound actions with operator (AND/OR)
    - Emits a warning for incorrectly defined actions (e.g., missing action name)
    """

    with open(filename, "r") as f:
        data = yaml.safe_load(f) or {}

    actions = data.get("actions", {})
    parsed_actions = {}

    def normalize_action(action):

        if isinstance(action, str):
            return (action, None)
        elif isinstance(action, (list, tuple)):
            if len(action) != 2:
                raise ValueError(f"List/tuple must have exactly 2 elements: {action}")


            return (action[0], action[1])
        elif isinstance(action, dict):
            if "call" in action:
                return ("call", action["call"], action.get("expected", "Success"))
            if "command" not in action:
                raise ValueError(f"Dict action missing 'command': {action}")

            return (action["command"], action.get("expected"))
        else:
            raise ValueError(f"Unsupported action format: {action}")

    for action_name, action_content in actions.items():
        parsed_actions[action_name] = []

        # Print warning if numeric action found without a name
        if isinstance(action_name, int) or (isinstance(action_name, str) and action_name.isdigit()):
            print(f"[WARNING] Possible wrongly defined action in actions.yaml: action name {action_name} : {action_content}")

        if isinstance(action_content, list) or (isinstance(action_content, dict) and 1 not in action_content):
            action_content = {1: action_content}
            
        if isinstance(action_content, dict):
            
            for key, value in action_content.items():
                if isinstance(value, dict) and "operator" in value:
                    operator = value["operator"]
                    if operator not in ("AND", "OR"):
                        raise ValueError(f"Unsupported operator: {operator}")
                    sub_actions = [normalize_action(v) for k, v in sorted(value.items()) if k != "operator"]
                    parsed_actions[action_name].append((operator, *sub_actions))
                else:
                    parsed_actions[action_name].append(normalize_action(value))
        else:
            raise ValueError(f"Unsupported action block format for '{action_name}': {action_content}")

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

    # If lab_name is not provided, ask the user
    if not args.lab_name:
        while True:
            try:
                lab_input = input("Enter lab name or path: ").strip()

                lab_base = os.path.basename(lab_input)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", lab_base):
                    print("Invalid lab name! Only letters, numbers, underscores, and dashes are allowed.")
                    continue

                # Determine the lab folder (full path)
                if os.path.isabs(lab_input):
                    lab_folder = lab_input
                elif os.path.exists(lab_input):
                    lab_folder = os.path.abspath(lab_input)
                else:
                    # Treat as lab name: look in ./labs/
                    lab_folder = os.path.join(script_dir, "labs", lab_input)

                # Check if lab_conf.yaml exists
                lab_conf_file = os.path.join(lab_folder, "lab_conf.yaml")
                if not os.path.isfile(lab_conf_file):
                    print(f"Lab '{lab_input}' not found at {lab_folder}. Please try again. (Ctrl + C to exit)")
                    continue

                # Store full path in lab_folder
                args.lab_name = lab_folder
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

def sanitize_filename(file_name: str) -> str:
    # Extract only the basename (the last part of the path)
    file_base = os.path.basename(file_name)
    # Remove any character that is not alphanumeric, underscore, or dash
    file_clean = re.sub(r'[^A-Za-z0-9_-]', '', file_base)
    return file_clean

def setup_command_history(lab_name: str):
    """
    Initialize readline history and tab completion for the lab.
    Returns the history file path so it can be deleted later.
    """
    safe_lab_name = sanitize_filename(lab_name)
    
    home_dir = os.path.expanduser("~")

    history_file = os.path.join(home_dir, f"kathara_{safe_lab_name}_history")

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
                                    command=["sh", "-c", command],
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
      action kali test r1 -a    -> runs 'test' and all commands of r1
      action kali -a r2 testr2  -> runs all actions of kali and 'testr2' on r2
    """
    if not args:
        print("You must specify at least one machine name.")
        return

    if args[0] not in lab_devices:
        print(f"Syntax error: first argument must be a machine. Got '{args[0]}'")
        return
    # Build targets: { machine: [action1, action2] }
    targets = {}
    current_machine = None

    i = 0
    n = len(args)

    while i < n:
        token = args[i]

         # Machine selection
        if token in lab_devices:
            # Before switching machine, ensure the previous one had actions
            if current_machine is not None and not targets[current_machine]:
                print(f"Syntax error for machine '{current_machine}': no actions defined.")
                return

            current_machine = token
            targets.setdefault(current_machine, [])

            i += 1

            # If the machine is the last token -> syntax error (missing action)
            if i >= n:
                print(f"Syntax error for machine '{current_machine}': no actions defined.")
                return

            continue

        # '-a' means: run all actions for this machine
        if token == "-a":
            if current_machine is None:
                print("Syntax error: '-a' must follow a machine.")
                return

            targets[current_machine] = list(actions.keys())
            i += 1
            continue

        # Token = action name
        if current_machine is None:
            print(f"Syntax error: action '{token}' without a machine.")
            return

        if token not in actions:
            print(f"\nAction '{token}' not found. Skipping.")
        else:
            targets[current_machine].append(token)

        i += 1

    # Final validation: last machine must have at least one action
    if current_machine is not None and not targets[current_machine]:
        print(f"Syntax error: machine '{current_machine}' has no valid actions.")
        return

    for machine, action_list in targets.items():

        for action_name in action_list:
            result, total_time, commands_log = run_action(machine, action_name)

            logger.save_action_log_yaml(
                machine=machine,
                action_result=result,
                total_time=round(total_time, 2),
                action_name=action_name,
                commands=commands_log
            )

            print(f"ACTION {action_name} on {machine}: {result}, see logs for more infos\n")


def run_action(machine, action_name):
        """
        Executes an action and returns:
           (result, time, commands_log)
        where result ∈ {"Success", "Fail"}
        """
        commands = actions[action_name]
        commands_log = {}
        action_result = True
        action_time = 0

        print(f"\nExecuting action '{action_name}' on {machine}\n")

        for idx, command in enumerate(commands, 1):

            # ---------------------------------------------
            # CASE: compound command (AND / OR)
            # ---------------------------------------------
            if isinstance(command, tuple) and command[0].upper() in ("AND", "OR"):
                operator = command[0].upper()
                sub_commands = command[1:]
                success = operator == "AND"
                parent_label = idx

                commands_log[parent_label] = {
                    "operator": operator,
                    "group_time": 0,
                    "group_result": "Success"
                }

                for sub_idx, (sub_cmd, expected) in enumerate(sub_commands, 1):
                    label = f"{idx}{chr(96 + sub_idx)}"

                    start = time.time()
                    try:
                        stdout, stderr, code = exec_command(machine, sub_cmd)
                    except Exception as e:
                        stdout, stderr, code = None, str(e), 1
                    elapsed = round(time.time() - start, 2)
                    action_time = round(action_time + elapsed, 2) 
                    commands_log[parent_label]["group_time"] = round(commands_log[parent_label]["group_time"] + elapsed, 2)

                    if stdout:
                        output = stdout.decode().strip()
                    elif stderr:
                        output = stderr.strip()
                    else:
                        output = ""
                    
                    commands_log[parent_label][label] = {
                        "command": sub_cmd,
                        "expected": expected,
                        "output": output,
                        "command_time": elapsed,
                        "result": "Success"
                    }

                    # evaluation
                    if code != 0 or (expected and operator=="AND" and expected not in output):
                        success = False
                        commands_log[parent_label][label]["result"] = "Fail"
                        break
                    if operator=="OR" and expected and expected in output:
                        success = True
                        break

                if not success:
                    commands_log[parent_label]["group_result"] = "Fail"
                    return ("Fail", action_time, commands_log)

                continue

            # ---------------------------------------------
            # CASE: CALL — run a sub-action
            # ---------------------------------------------
            if command[0] == "call":
                _, called_action, expected = command

                print(f"Calling action '{called_action}' inside '{action_name}\n'")

                sub_result, sub_time, sub_log = run_action(machine, called_action)
                action_time += sub_time

                commands_log[idx] = {
                    "call": called_action,
                    "expected": expected,
                    "result": "Success" if sub_result == expected else "Fail",
                    "action_time": sub_time,
                    "commands": sub_log, 
                }

                if sub_result != expected:
                    return ("Fail", action_time, commands_log)

                continue

            # ---------------------------------------------
            # CASE: simple command
            # ---------------------------------------------
            cmd_str, expected = command
            start = time.time()
            try:
                stdout, stderr, code = exec_command(machine, cmd_str)
            except Exception as e:
                stdout, stderr, code = None, str(e), 1

            elapsed = round(time.time() - start, 2)
            action_time += round(elapsed, 2)

            if stdout:
                output = stdout.decode().strip()
            elif stderr:
                output = stderr.strip()
            else:
                output = ""

            commands_log[idx] = {
                "command": cmd_str,
                "expected": expected,
                "output": output,
                "command_time": elapsed,
                "result": "Success"
            }

            if code != 0 or (expected and expected not in output):
                commands_log[idx]["result"] = "Fail"
                return ("Fail", action_time, commands_log)

        return ("Success", action_time, commands_log)

def cmd_plan():
    pass


cmd_commands = {
    "exit": cmd_exit,
    "help": cmd_help,
    "status": cmd_status,
    "terminal": cmd_terminal,
    "deploy": cmd_deploy,
    "undeploy": cmd_undeploy,
    "restart": cmd_restart,
    "action": cmd_action,
    "plan" : cmd_plan
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
        logger = ActionLogger(lab_folder)
        #logger = ActionLogger()
        # Load lab configuration
        lab_info, devices = load_lab(os.path.join(lab_folder, "lab_conf.yaml"))
        lab_name = lab_info.get("description")
        if os.path.isfile(os.path.join(lab_folder,"actions.yaml")):
            try: 
                actions = parse_actions(os.path.join(lab_folder,"actions.yaml"))
                actions = { str(k): v for k, v in actions.items() }
            except Exception as e:
                print(e)
                exit()
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

            #Create 
            #machines_names = []
            #machines_names.append(name)

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