# Architecture

## System Overview

KathaRange is a framework built on top of Kathara API.
It provides automated lab deployment, action execution, plan orchestration, and structured logging within a controlled lab environment.

The system is designed around clear separation of responsibilities between infrastructure management, execution logic, and logging.

Core concepts:

* **Lab** – Virtual network environment composed of machines and links
* **Action** – Ordered set of commands executed on a machine
* **Plan** – Orchestration of multiple actions and/or commands
* **Command** – Single executable shell instruction
* **Log** – Structured YAML trace of execution

---

## Architectural Layers

The system follows a layered architecture to ensure modularity and maintainability.

### Infrastructure Layer

Responsible for provisioning and managing the virtual lab environment.

#### Responsibilities

* Load lab configuration
* Instantiate machines
* Configure network topology
* Connect interfaces and links
* Copy assets and startup files
* Deploy and undeploy lab
* Optional OSPF deployment and convergence validation

#### Main Components

* `LabManager`
* `OSPFManager`
* `Kathara`

This layer abstracts Docker/Kathara details from the execution engine.

---

### Execution Layer

Responsible for interpreting YAML definitions and orchestrating execution.

#### Responsibilities

* Parse `actions.yaml`
* Parse `plans.yaml`
* Inject parameters
* Execute commands inside machines
* Evaluate expected results
* Handle logical operators (AND / OR)
* Aggregate execution results

#### Main Component

* `CommandManager`
* `action_parser`
* `plan_parser`

This layer acts as the orchestration engine of the system.

---

### Logging Layer

Responsible for structured and reproducible execution tracing.

#### Responsibilities

* Generate YAML logs for actions and plans
* Automatically create directory structure if missing
* Store execution metadata
* Record timing information
* Record expected vs actual output
* Ensure proper ownership when running with sudo

#### Components

* `ActionLogger`
* `PlanLogger`

Logs are deterministic, structured, and machine-readable.

---

### Interface Layer

Responsible for user interaction and runtime control.

#### Responsibilities

* Parse CLI arguments
* Handle interactive command input
* Spawn terminals for machines
* Monitor running processes
* Dispatch commands to execution engine

#### Utilities

* `parse_args`
* `spawn_terminal`
* `monitor_processes`

This layer provides the operational interface without embedding execution logic.

---

## Startup Workflow

When the system starts:

1. CLI arguments are parsed
2. Lab configuration is loaded
3. Actions and plans are parsed
4. Previous lab instance is undeployed (if present)
5. Lab topology is created
6. Machines are configured and connected
7. Assets and startup files are injected
8. Lab is deployed via Kathara
9. CommandManager is initialized
10. Interactive loop begins

---

## Runtime Execution Flow

### Action Execution Flow

1. User triggers an action
2. CommandManager retrieves action definition
3. Parameters are resolved
4. Commands are executed sequentially
5. Expected outputs are validated
6. Logical groups (AND / OR) are evaluated
7. Final result is computed
8. Action log is generated

### Plan Execution Flow

1. User triggers a plan
2. Prerequisite steps (`need`) are executed
3. If prerequisites succeed, main actions run
4. Each action generates its own detailed breakdown
5. Plan aggregates step results
6. Plan log is generated

---

## Infrastructure Command Flow

In addition to actions and plans, the system supports direct infrastructure commands triggered via CLI, such as:

- `deploy`
- `undeploy`
- `restart`
- `status`
- `terminal`
- `exit`

These commands interact directly with the **Infrastructure Layer** and at the moment they do **not** generate logs.

### Command Data Flow
```
User Input (CLI)
    ↓
CLI Parser / CommandManager
    ↓
Infrastructure Layer (LabManager / Kathara)
    ↓
Lab / Machines / Network
    ↓
Output to Terminal
```
---
## Action/Plan Data Flow

High-level execution data flow:

```
User Input
    ↓
CommandManager
    ↓
Action / Plan Engine
    ↓
Command Execution (inside machine)
    ↓
Result Evaluation
    ↓
Logger
    ↓
YAML Log File
```

This flow guarantees full traceability from user input to persisted log.

---

## Directory Structure Logic

The architecture enforces a structured directory organization:

```
<lab>/
├── actions.yaml
├── plans.yaml
├── assets/
├── startups/
└── logs/
```

Logs are automatically created if the directory hierarchy does not exist.

This ensures:

* No manual preparation required
* Consistent structure across labs
* Clean separation between configuration and runtime artifacts

---

## Design Principles

The architecture is based on the following principles:

### YAML-Driven Configuration

Execution behavior is defined declaratively through YAML files.

### Separation of Concerns

Infrastructure, execution logic, logging, and user interaction are isolated in dedicated components.

### Deterministic Execution

Expected outputs are explicitly defined and validated.

### Reproducibility

Each execution of actions and plans is fully traceable via timestamped logs.

### Automatic Environment Preparation

Required directories and runtime structures are automatically created.

---

## Extensibility

The architecture allows future extensions such as:

* Additional routing protocols
* New execution operators
* Extended validation mechanisms

Because components are modular, new features can be added without impacting unrelated layers.

---

