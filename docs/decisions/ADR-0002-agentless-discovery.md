\# ADR-0002: Discovery Must Not Require an Agent



\## Status



Accepted



\## Date



2026-07-28



\## Context



BEACN should provide immediate visibility of a network after installation. Users should not need to install an agent before devices appear in the Console.



\## Decision



Network discovery will always be agentless.



The Discovery Engine will gather observable facts such as:



\- IP address

\- MAC address

\- Hostname

\- Vendor

\- Open ports

\- Basic operating system fingerprint

\- First Seen

\- Last Seen



Installing a BEACN Agent enriches an existing device rather than creating a new one.



\## Architectural Principle



Discovery collects facts.



Fingerprinting interprets those facts.



Agents enrich existing devices.



\## Consequences



\### Positive



\- Immediate visibility

\- Works with routers, printers, NAS devices and IoT equipment

\- Agent installation becomes optional



\### Negative



\- Some operating system detection will be approximate.

\- Devices blocking ICMP or ports may require alternative discovery methods.



\## Resulting Architecture



```text

Network Scan

&#x20;   │

&#x20;   ▼

Discovery Observations

&#x20;   │

&#x20;   ▼

Fingerprint Engine

&#x20;   │

&#x20;   ▼

Inventory Database

&#x20;   │

&#x20;   ├── Agent Installed → Enriched telemetry

&#x20;   └── No Agent → Basic inventory

```

