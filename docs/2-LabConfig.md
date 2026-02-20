# Lab Configuration

KathaRange uses **YAML configuration files** (`lab_conf.yaml`) to define **laboratory topologies**.
These files allow users to declare **nodes, network interfaces, IP addresses, services, and optional properties**, enabling the deployment of fully customizable cyber-range environments.

---

## Lab Metadata

Each lab configuration file begins with metadata:

```yaml
description: "Lab description"
version: "1.0"
author: "Author Name"
```

* **description**: Short description of the lab scenario.
* **version**: Version of the configuration.
* **author**: Creator or maintainer of the lab.

---

## Devices

The `devices` section lists all machines in the lab. Each device can represent a router, server, workstation, or security tool.

Each device entry typically includes:

* **image**: Docker image to deploy
* **type**: Node type (e.g., router, server, workstation)
* **interfaces**: Mapping of virtual NICs to network segments
* **addresses**: IP addresses for each interface

This structure allows the orchestration engine to deploy **modular and flexible lab topologies**.

---

## Device Categories

Devices are usually grouped logically:

1. **Routers** – Connect networks and forward traffic
2. **Servers** – Host applications or vulnerable services for testing
3. **Workstations** – Represent endpoints, including attackers and regular users
4. **Security & Monitoring Nodes** – IDS, SIEM, or adversary simulation tools

---

## Optional Device Properties

Devices can include **optional fields** to customize behavior and environment. Common optional fields include:

* **spawn_terminal**: Automatically opens a terminal on startup for interaction
* **assets**: Copy files into the container root (`/`); if not set, defaults to `<lab_name>/assets/<device_name>/` (if it exists)
* **options**: Additional configurations, e.g., `bridged: true`
* **envs**: Environment variables required for service configuration
* **ports**: Host-to-container port mappings for external access
* **ulimits**: Resource limits such as memory and file descriptors

---

## Networks and Interfaces

* Networks are defined implicitly through interface mappings
* Routers connect different network segments to enable communication
* Devices can be bridged to the host network when needed
* IP addresses should be defined consistently to avoid conflicts

---

## Startup files

The `startups` folder can contain custom startup files for devices. Each file should be named `<device_name>.startup` to match the corresponding device configuration.

---

## Extending Labs

To create or modify a lab:

1. Copy an existing YAML configuration or start from a new file
2. Define all nodes in the `devices` section
3. Assign network interfaces and IP addresses
4. Specify Docker images and optional properties (ports, environment variables, ulimits)
5. Update metadata (description, version, author)
6. If not previously defined in a device configuration, copy necessary assets in right folder (e.g., new certificates for Wazuh)
7. Define startup files (optional)
8. Define actions and plans for automation (optional)
9. Launch the lab and verify the configuration

This approach enables **arbitrary lab topologies**, from small experiments to complex enterprise-like environments.


