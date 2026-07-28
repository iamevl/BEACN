\# ADR-0003: Device as the Core Domain Object



\## Status



Accepted



\## Date



2026-07-28



\## Context



BEACN receives information about network-connected equipment from several sources:



\- Agentless network discovery

\- Windows nodes

\- Linux nodes

\- Docker telemetry

\- Manual inventory entries

\- Future integrations such as SNMP, cloud platforms and hypervisors



Without a shared representation, each source could create its own version of a machine, causing duplicate records, inconsistent identifiers and tightly coupled features.



BEACN needs a stable domain object that represents a physical, virtual or embedded network-connected device regardless of how it was discovered.



\## Decision



The Device will be the central domain object in BEACN.



Discovery, agents and integrations will contribute observations to an existing Device record rather than maintaining independent inventories.



A Device may exist without:



\- A BEACN agent

\- A known hostname

\- A known operating system

\- A permanent IP address

\- Management credentials



Agent installation will enrich an existing Device record rather than create a separate device.



\## Core Device Information



A Device may contain:



\### Identity



\- Internal BEACN device ID

\- Hostname

\- Display name

\- IP addresses

\- MAC addresses

\- Vendor

\- Serial number

\- Device type



\### Discovery



\- First seen

\- Last seen

\- Discovery sources

\- Reachability

\- Observed services

\- Open ports

\- Fingerprint confidence



\### Agent



\- Agent installed

\- Agent identifier

\- Agent type

\- Agent version

\- Last check-in

\- Agent health



\### Platform



\- Operating system

\- Operating system version

\- Architecture

\- Manufacturer

\- Model



\### Telemetry



\- CPU

\- Memory

\- Storage

\- Network

\- Temperatures

\- Services

\- Docker



\### Management



\- Tags

\- Notes

\- Groups

\- Alerts

\- Available actions



\## Identity Principle



IP addresses and hostnames are observations, not permanent identities.



Every Device will receive an internal immutable BEACN device ID.



BEACN will use available identifiers to associate new observations with existing devices, including:



\- Agent identifier

\- MAC address

\- Serial number

\- Hostname

\- IP address



Matching will use confidence levels because individual identifiers may change, be duplicated or be unavailable.



\## Architectural Responsibilities



\- Discovery creates observations and may create a Device.

\- Device matching associates observations with Devices.

\- Agents enrich Devices with authoritative telemetry.

\- The inventory stores Devices and their observations.

\- The Console presents Devices.

\- Alerts and management actions belong to Devices.



\## Consequences



\### Positive



\- All BEACN features share one inventory.

\- Agentless and agent-managed equipment appear together.

\- Devices can gain richer information over time.

\- New discovery and integration methods can be added without redesigning the Console.

\- Device history can survive DHCP and hostname changes.



\### Negative



\- Device matching requires careful rules.

\- Duplicate detection cannot rely solely on IP addresses.

\- Some matches will require confidence scoring.

\- Inventory migrations will be needed as the model evolves.



\## Resulting Flow



```text

Discovery / Agent / Integration

&#x20;             |

&#x20;             v

&#x20;        Observation

&#x20;             |

&#x20;             v

&#x20;       Device Matching

&#x20;             |

&#x20;             v

&#x20;          Device

&#x20;             |

&#x20;      +------+------+

&#x20;      |             |

&#x20;      v             v

&#x20;  Inventory      Telemetry

&#x20;      |             |

&#x20;      +------+------+

&#x20;             |

&#x20;             v

&#x20;          Console

