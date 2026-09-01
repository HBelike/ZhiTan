# Security Policy

## Supported versions

ZhiTan is currently a developer preview. Security fixes are applied to the latest `master` revision; older commits and self-modified deployments are not separately supported.

## Reporting a vulnerability

Please use GitHub's **Private vulnerability reporting** feature for this repository. Do not open a public issue for suspected vulnerabilities and do not include secrets, personal data, resumes, transcripts, cookies, or production logs in public discussions.

Include only what is needed to reproduce and assess the issue:

- affected commit or version;
- affected component and deployment topology;
- prerequisites and minimal reproduction steps;
- expected and observed security impact;
- a suggested mitigation, if available;
- sanitized logs or proof of concept.

Please avoid destructive testing, persistence, denial of service, access to data that is not yours, or automated scanning of third-party services. Give maintainers reasonable time to investigate and publish a fix before disclosure.

## Deployment responsibilities

Operators are responsible for TLS, host patching, database backups, access controls, provider-account permissions, and secret rotation. Keep authentication enabled on public instances, use a unique database password and credential-encryption key, configure the administrator email through the environment, and never expose PostgreSQL or document-processing services directly to the internet.

If a credential reaches Git history, logs, an issue, or a build artifact, revoke and rotate it immediately. Removing the text alone does not make the credential safe again.
