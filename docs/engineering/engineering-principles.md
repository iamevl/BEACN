# BEACN Engineering Principles

Version: 1.0

These principles guide architectural and engineering decisions throughout the BEACN project. They are intentionally stable and should change rarely.

---

##  0. Simplicity First

Every design decision should make BEACN easier to understand, easier to deploy and easier to operate.

If two solutions solve the same problem, prefer the simpler one.

---

## 1. Device First

A device is the core entity within BEACN.

Everything else exists to discover, describe, monitor or manage a device.

---

## 2. Identity is Permanent

A device is identified by its UUID.

IP addresses, hostnames and MAC addresses are attributes, not identity.

---

## 3. Observations are Facts

Discovery engines, Windows Agents, Docker, SNMP and future collectors produce observations.

Observations describe devices.

They do not define them.

---

## 4. Live Data is Ephemeral

Telemetry represents the current state of a device.

Inventory represents long-term knowledge.

These are intentionally separate.

---

## 5. Documentation Evolves with Code

Architectural changes must include documentation updates.

The repository is the source of truth.

---

## 6. One Source of Truth

Information should exist in one place.

Avoid duplicate models, duplicated configuration and duplicated business logic.

---

## 7. Build for Tomorrow

Every feature should make future features easier.

Avoid shortcuts that create technical debt.

---

Every release should leave BEACN simpler, more consistent, and easier to extend than the release before it.
