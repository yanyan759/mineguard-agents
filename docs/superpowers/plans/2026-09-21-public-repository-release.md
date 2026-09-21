# Public Repository and Release Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the public MineGuard Agents repository small and reproducible while documenting how optional large data is distributed through sources or GitHub Releases.

**Architecture:** The Git repository contains executable source, Compose files, templates, small fixtures, and documentation. Large or licensed materials stay outside Git and are referenced by a manifest; a repository audit script checks tracked paths and file sizes before publication.

**Tech Stack:** Git, PowerShell/Python, Markdown, Docker Compose, existing MineGuard Agents tests.

## Global Constraints

- Modify only `agent1/`; do not modify the historical `code/` directory.
- Do not add private, unlicensed, raw mine, generated, cache, dependency, or credential files to the public repository.
- The default clone must run the frontend/backend demo without optional external corpus files.
- Release assets are optional supplements and must include source, license, provenance, and checksum information.

---

### Task 1: Add a public package audit

**Files:**
- Create: `scripts/check_public_package.py`
- Test: command-line audit against the current Git index

**Interfaces:**
- Consumes: Git index from the repository root.
- Produces: exit code 0 when tracked files satisfy the public-package policy; nonzero output with offending paths otherwise.

- [x] **Step 1: Implement the audit**

  Check tracked files only, reject files over 100 MiB, and reject tracked paths containing dependency directories, runtime output, credentials, or known raw corpus extensions under research-data directories. Print a machine-readable summary suitable for CI.

- [x] **Step 2: Run the audit**

  Run `python scripts/check_public_package.py` from `agent1/` and confirm it reports the tracked-file count and no violations.

### Task 2: Document repository and Release boundaries

**Files:**
- Create: `docs/release-and-data-distribution.md`
- Modify: `README.md` section 17 and documentation index
- Modify: `docs/external-data.md` with a link to the release policy

**Interfaces:**
- Consumes: existing `.gitignore`, `docs/public-data-manifest.json`, and external-data instructions.
- Produces: user-facing instructions distinguishing source downloads, tagged Releases, optional data, licenses, checksums, and Docker image distribution.

- [x] **Step 1: Write the release guide**

  Explain what a Release is, what belongs in the source repository, what may be a Release asset, what must remain external, and the exact clean-room reproduction path.

- [x] **Step 2: Link the guide from existing documentation**

  Add the guide to README navigation and the source-data documentation without duplicating long instructions.

- [x] **Step 3: Check documentation consistency**

  Confirm the README says the default clone runs with small fixtures and that large data is optional.

### Task 3: Validate and commit the public package

**Files:**
- Modify: `docs/superpowers/plans/2026-09-21-public-repository-release.md` checklist state

- [x] **Step 1: Run repository audit and relevant validation**

  Run the public-package audit, fixture validation, and frontend build when dependencies are available.

- [x] **Step 2: Inspect the final Git diff**

  Confirm only `agent1/` documentation and audit files changed, no large files are staged, and the remote remains `https://github.com/yanyan759/mineguard-agents.git`.

- [ ] **Step 3: Commit and push**

  Commit with `docs: clarify public package and release data policy`, then push `main` after checks pass.
