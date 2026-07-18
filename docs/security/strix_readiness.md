# Strix readiness

## Status

**Researched, documented, scaffolded and inactive. Strix is not installed and has not been executed.** No scan, Docker action, LLM request, target access, proof of concept, result generation or CI integration was performed.

This document records future decision requirements. It is not authorization and contains no launcher.

## Official source record

- Repository: [`usestrix/strix`](https://github.com/usestrix/strix), owned by the `usestrix` GitHub organization.
- Reviewed commit: `7d5a67d234bd3faef34d22be8c6f5a9607de41a3`, `main`, accessed 2026-07-19.
- Latest observed release: `v1.1.0`, published 2026-07-14.
- License: Apache License 2.0; upstream notice names Copyright 2025 OmniSecure Inc.
- Full reviewed paths and notice: [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).

## Purpose and deferred timing

Strix describes itself as an agentic penetration-testing tool that can inspect source, exercise applications dynamically, use offensive tools and validate findings with proofs of concept. That capability is disproportionate to the current static informational site, which has no backend, API integration or real game data. A real assessment is considered only after an owned backend, API or other dynamic attack surface exists and after every required approval is recorded separately.

## Upstream prerequisites and operational effects

- Local use requires Python 3.12 or later, the Strix package, its dependencies, a running Docker-compatible runtime and an LLM provider or approved local-model path.
- The documented default runtime is Docker and default sandbox image is `ghcr.io/usestrix/strix-sandbox:1.0.0`; the first run can pull an image.
- The Kali-based sandbox includes reconnaissance, port scanning, crawling, fuzzing, exploitation, proxy, browser, secret-detection, source-analysis and supply-chain tools. Examples include Nmap, Nuclei, SQLMap, ZAP, Caido, Playwright, Semgrep, Gitleaks and TruffleHog.
- Targets may be local directories, repositories, URLs, domains or IP addresses. Runs can copy a local directory or bind-mount one read-only; upstream warns that a root process inside the container can remount it, so the mount is not a hard isolation boundary.
- Modes are `quick`, `standard` and `deep`; `deep` is the documented default. Standard and deep include source-aware triage followed by dynamic validation.
- Strix can generate working proofs of concept and remediation material. Such behavior may mutate a target or affect availability and must be explicitly scoped.
- Local results are documented under `strix_runs/<run-name>`, including local event telemetry. Results for this project must instead be directed to an approved location outside Git, normally `D:\coc\runs`, with the exact path approved before execution.

## LLM, network and data egress

The tool sends model inputs to the selected LLM provider unless an approved local model is used. Source, instructions, application responses, vulnerability context or findings may therefore cross a trust boundary. Provider choice, model, credentials, data classes, retention terms, geographic/organizational boundary and allowed egress must be reviewed before use.

Upstream telemetry is enabled by default and documents PostHog and Scarf collection of basic usage metadata. `STRIX_TELEMETRY=0` is documented as the opt-out. Optional Perplexity enables web search. Optional remote OTEL variables enable remote trace export. Future review must require telemetry disabled, no web-search key and no remote OTEL export unless separately and explicitly approved. LLM traffic remains separate data egress even when product telemetry is disabled.

## Configuration and secrets

Upstream documents a JSON file with an `env` object and also loads environment variables, with environment variables taking precedence. It does not publish a standalone, exhaustive JSON Schema that constrains every accepted key and value. Therefore this project does not provide `config.example.json`.

No credentials belong in Git, scope examples, command history, findings or reports. A future local config must use an ignored path such as `security/strix/config.local.json` or `.strix`, contain only approved values, use restrictive filesystem permissions, and be removed or rotated after use as applicable. LLM, provider, application and test credentials each need separate approval.

## Scope policy

Potentially allowed only after explicit authorization:

- source code owned by the user and located inside the exact approved local project path;
- a loopback-only application started for the assessment under a separately approved command;
- an isolated test environment owned by the user and explicitly authorized by its exact host, paths, time window and mode.

Even a loopback or localhost target requires a separate recorded authorization for the exact target, mode and command.

Always forbidden under this readiness scaffold:

- GitHub Pages, the production or public Clash Clan Analytics site;
- any other domain, repository, API or IP address;
- Supercell infrastructure and the Clash of Clans API;
- GitHub infrastructure;
- internet reconnaissance, bug-bounty targets or third-party systems;
- `D:\work`, `D:\study`, unrelated local paths and personal browser sessions.

An allowed scope never expands through redirects, links, DNS results, discovered hosts, dependencies or content returned by a target.

## Separate approval matrix

Each item requires its own recorded decision. Approval of one item does not imply another.

1. Specific Strix version.
2. Strix installation.
3. Python dependency installation.
4. Docker Desktop or another runtime.
5. `docker pull`.
6. Sandbox image or container creation.
7. `docker run`.
8. LLM provider selection.
9. API key creation or use.
10. Sending source code to an external LLM.
11. Local LLM use.
12. Network access.
13. Exact target.
14. Exact scan mode.
15. Exact command.
16. Result creation.
17. Reading and processing findings.
18. Any future CI/CD integration.
19. Any production scan.
20. Any external-system scan.

## Stop conditions

Stop immediately and preserve existing evidence without retry if the target differs from the recorded scope, a redirect or discovery leaves scope, production or a third party is reached, credentials or personal data appear unexpectedly, data egress differs from approval, telemetry or external search is active unexpectedly, Docker isolation differs from the reviewed plan, the target becomes unstable, cost or time limits are reached, results cannot be kept outside Git, or any approver withdraws authorization.

## Future execution checklist

- [ ] A real dynamic attack surface exists and security testing is proportionate.
- [ ] `security/strix/scope.example.md` has been copied to an ignored local file and every field completed.
- [ ] All 20 approval decisions are recorded separately.
- [ ] Exact source revision or release, package provenance and hashes are reviewed again.
- [ ] Docker runtime, image, mounts, capabilities, network and cleanup plan are reviewed.
- [ ] LLM provider, model, credentials, egress, retention and cost ceiling are approved.
- [ ] Telemetry, web search and remote trace export are disabled unless explicitly approved.
- [ ] Allowed and excluded targets are technically enforceable.
- [ ] Results and logs resolve to an approved ignored path outside Git.
- [ ] Backup, stop, incident, credential-rotation and cleanup procedures are ready.
- [ ] The exact command receives separate approval immediately before execution.
- [ ] Findings handling receives separate approval before results are opened or processed.

## No CI

No workflow, scheduled task, hook or automatic scan is prepared. Future CI/CD integration is a separate decision and is intentionally deferred.
