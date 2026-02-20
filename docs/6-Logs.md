# Logs

KathaRange automatically generates structured **YAML logs** for both **actions** and **plans**.

Logs provide full traceability of execution, including:

* Executed commands
* Expected values
* Actual output
* Execution time
* Final result (Success / Fail)

All logs are stored inside the lab directory under the `logs/` folder.  
If the `logs/`, `actions/`, or `plans/` folders do not exist, they are automatically created at runtime.

---

## Log Directory Structure

Logs are organized as follows:

```
<lab_path>/logs/

├── actions/
│   └── <machine>/
│       └── <action_name>/
│           └── <action_name>_<timestamp>.yaml
│
└── plans/
    └── <plan_name>/
        └── <plan_name>_<timestamp>.yaml
```

Each execution generates a new timestamped YAML file.

### File Name Format

Plan log file name:

```
<plan_name>_<YYYYMMDD_HHMMSS>.yaml
```

Example:

```
my_plan_20260209_103137.yaml
```

Action log file name:

```
<action_name>_<YYYYMMDD_HHMMSS>.yaml
```

Example:

```
test_20260218_111157.yaml
```

The file name is always composed of:

* The action or plan name
* Date (YYYYMMDD)
* Time (HHMMSS)

This guarantees uniqueness and chronological ordering.

Timestamp format:

```
YYYYMMDD_HHMMSS
```

Example:

```
20260218_111157
```

---

## Action Logs

Action logs are generated after executing a single action.

They are saved under:

```
logs/actions/<machine>/<action_name>/
```

### Action Log Structure

Example:

```yaml
action_name: test
timestamp: '20260218_111157'
total_time: 3.56
final_result: Fail
commands:
  1:
    operator: AND
    group_time: 3.56
    group_result: Fail
    1a:
      command: nmap -sV 10.10.1.1 | fgrep 'Apache' | awk '/open/'
      expected: Apache
      output: ''
      command_time: 3.56
      result: Fail
```

### Fields

* `action_name` – Name of the executed action
* `timestamp` – Execution time identifier
* `total_time` – Total execution time (seconds)
* `final_result` – Overall result of the action (Success / Fail)
* `commands` – Detailed execution breakdown

### Command-Level Fields

For each command:

* `command` – Executed shell command
* `expected` – Expected output string
* `output` – Actual command output
* `command_time` – Execution time (seconds)
* `result` – Success / Fail

### Compound Commands (AND / OR)

If the action contains logical operators:

* `operator` – AND / OR
* `group_time` – Total time for the group
* `group_result` – Final result of the group
* Nested steps (1a, 1b, …) contain individual command logs

---

## Plan Logs

Plan logs are generated after executing a full plan.

They are saved under:

```
logs/plans/<plan_name>/
```

### Plan Log Structure

Example:

```yaml
plan_name: my_plan
timestamp: '20260209_103100'
total_time: 3.94
final_result: Fail
steps:
  need:
    success: true
    steps:
      1:
        type: action
        action: testr2
        machine: kali
        expected: Success
        result: Success
        time: 0.33
        commands:
          1:
            command: echo "test r2"
            expected: test r2
            output: test r2
            command_time: 0.15
            result: Success
  actions:
    success: false
    steps:
      1:
        type: action
        action: test_plan
        machine: kali
        expected: Success
        result: Fail
        time: 3.51
        commands:
          1:
            operator: AND
            group_time: 3.51
            group_result: Fail
            1a:
              command: nmap -sV 192.168.2.10 | fgrep 'Apache' | awk '/open/'
              expected: Apache
              output: ''
              command_time: 3.51
              result: Fail
```

### Plan-Level Fields

* `plan_name` – Name of the executed plan
* `timestamp` – Execution identifier
* `total_time` – Total execution time (seconds)
* `final_result` – Overall plan result of the plan
* `steps` – Breakdown of execution

---

### `need` Section

* Contains prerequisite steps
* Includes:

  * `success` – Whether all prerequisites passed
  * `steps` – Detailed log per prerequisite step

Each step contains:

* `type` – action or command
* `machine` – Machine executing the step
* `expected` – Expected result
* `result` – Step result
* `time` – Execution time
* `commands` – If type is action, full action breakdown

---

### `actions` Section

* Contains main execution steps
* Same structure as `need`
* Includes overall `success` flag

---

## Ownership and Permissions

If the lab is executed using `sudo`, log files and folders are automatically reassigned to the original user (using `SUDO_UID` and `SUDO_GID`).

This ensures:

* No root-owned log files
* Proper file access after execution

---

## Purpose of Logs

Logs provide:

* Full reproducibility of execution
* Debugging information
* Execution timing analysis
* Forensic traceability
* Structured machine-readable output (YAML)

---

