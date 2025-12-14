from src.command_system.utils import handle_errors
from Kathara.manager.Kathara import Kathara
import time

def exec_command(cmd_manager, machine_name, command):
    try:
        stdout, stderr, code = Kathara.get_instance().exec(
                                    machine_name=machine_name,
                                    command=["sh", "-c", command],
                                    lab=cmd_manager.lab,
                                    stream=False
                            )
        return stdout, stderr, code
    except Exception as e:
        print(f"Exception while executing action on {machine_name}: {e}\n")
        return None , str(e), 1  

@handle_errors
def cmd_action(args, cmd_manager):
    """
    Execute actions for machines defined in the file actions.yaml.
    Usage: action <machine1> <action1> <action2> <machine2> <action1>
    Examples:
      action kali test          -> runs 'test' on kali
      action kali -a            -> runs all actions on kali
      action kali test r1 -a    -> runs 'test' on kali and all actions on r1
      action kali -a r2 testr2  -> runs all actions on kali and 'testr2' on r2
    """
    if not args:
        print("You must specify at least one machine name.")
        return
    lab_devices = list(cmd_manager.lab.machines.keys())
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

            targets[current_machine] = list(cmd_manager.actions.keys())
            i += 1
            continue

        # Token = action name
        if current_machine is None:
            print(f"Syntax error: action '{token}' without a machine.")
            return

        if token not in cmd_manager.actions:
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
            result, total_time, commands_log = run_action(cmd_manager, machine, action_name)

            cmd_manager.logger.save_action_log_yaml(
                machine=machine,
                action_result=result,
                total_time=round(total_time, 2),
                action_name=action_name,
                commands=commands_log
            )

            print(f"\nACTION {action_name} on {machine}: {result}, see logs for more infos\n")


def run_action(cmd_manager, machine, action_name):
        """
        Executes an action and returns:
           (result, time, commands_log)
        where result ∈ {"Success", "Fail"}
        """
        commands = cmd_manager.actions[action_name]
        commands_log = {}
        #action_result = True
        action_time = 0

        print(f"\nExecuting action '{action_name}' on {machine}\n")

        for idx, command in enumerate(commands, 1):
            print(f"({idx}) -> {command}")
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
                        stdout, stderr, code = exec_command(cmd_manager, machine, sub_cmd)
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

                sub_result, sub_time, sub_log = run_action(cmd_manager, machine, called_action)
                action_time += sub_time

                commands_log[idx] = {
                    "call": called_action,
                    "expected": expected,
                    "result": "Success" if sub_result == expected else "Fail",
                    "action_time": sub_time,
                    "commands": sub_log, 
                }

                if sub_result != expected:
                    print(f"\nAction {called_action} failed\n")
                    return ("Fail", action_time, commands_log)
                print(f"\nAction {called_action} completed successfully. Returning to {action_name}\n")
                continue

            # ---------------------------------------------
            # CASE: simple command
            # ---------------------------------------------
            cmd_str, expected = command
            start = time.time()
            try:
                stdout, stderr, code = exec_command(cmd_manager, machine, cmd_str)
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