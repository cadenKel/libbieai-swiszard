# Security Policy

## How this code was made

All code in this repository is **AI-generated and human-orchestrated by a biologist**, not a career software engineer. The architecture and product decisions come from a human; the typing comes from an LLM agent. This means:

- We may ship subtle bugs a senior engineer would catch in review.
- We may miss a CVE in a transitive dependency.
- We may bake in a bad default. Please be loud about it.

We are committed to fixing security issues fast and crediting reporters publicly.

## Reporting a vulnerability

Please do not open a public issue for security problems. Instead:

- Email: security@libbie.ai
- Or open a GitHub issue with the security label and minimal detail; we will respond and ask for details privately.

What to include if possible:
- A description of the issue and where it lives in the code.
- A reproducer or proof-of-concept.
- The version (run: pip show libbieai-swiszard) and any relevant env.

## Response timeline

- We acknowledge within 72 hours.
- We aim to ship a fix within 14 days for high-severity issues.
- We will credit you in the changelog and the security advisory.

## In scope

- Anything that runs untrusted code, exfiltrates files/secrets, or weakens process isolation.
- Supply-chain weaknesses in dependencies or build steps.
- Memory-server endpoints exposed unsafely.
- Anything where a malicious task string could cause the router to do something outside its handler contract.

## Out of scope

- Issues that require physical access to the user machine.
- Bugs in third-party services (Ollama, Cloudflare, etc.) -- report those upstream.
- Theoretical issues without a reproducible attack.

Thank you for helping make this safer. Genuinely.
