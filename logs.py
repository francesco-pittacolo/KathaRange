import os
import yaml
from datetime import datetime

LOG_DIR = "logs"

def save_action_log_yaml(lab_path: str, machine: str, action_name: str, commands: dict):
    """
    Save all executed commands of a single action into one YAML log file.

    Parameters:
    - lab_path: root folder for logs
    - machine: machine name
    - action_name: name of the action
    - commands: dict mapping label -> {command, expected, output, return_code}

    Automatically creates directories:
        <lab_path>/logs/<machine>/

    Ownership is set to the original user if run with sudo, but only for newly created
    folders/files.
    """
    try:
        # UID/GID of original user
        uid = int(os.environ.get("SUDO_UID", os.getuid()))
        gid = int(os.environ.get("SUDO_GID", os.getgid()))

        # Ensure logs folder exists
        logs_dir = os.path.join(lab_path, LOG_DIR)
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
            try:
                os.chown(logs_dir, uid, gid)
            except PermissionError:
                pass

        # Ensure machine-specific folder exists
        machine_dir = os.path.join(logs_dir, machine)
        if not os.path.exists(machine_dir):
            os.makedirs(machine_dir)
            try:
                os.chown(machine_dir, uid, gid)
            except PermissionError:
                pass

        # Prepare file path
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{action_name}_{timestamp}.yaml"
        filepath = os.path.join(machine_dir, filename)

        # Wrap everything for YAML
        data = {
            "action_name": action_name,
            "timestamp": timestamp,
            "commands": commands
        }

        # Write YAML file
        with open(filepath, "w") as f:
            yaml.dump(data, f, sort_keys=False)

        # Chown the file itself
        try:
            os.chown(filepath, uid, gid)
        except PermissionError:
            pass

        return filepath

    except Exception as e:
        import traceback
        print(f"[ERROR] Failed to save log for {machine}/{action_name}: {e}")
        traceback.print_exc()
        return None
