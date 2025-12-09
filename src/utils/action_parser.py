import yaml

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