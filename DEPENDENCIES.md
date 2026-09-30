# Dependency verification — Queue Edition, 2026-10-01

This revision retains the compatible dependency lock from the deployed baseline
(verified 2026-09-30). The queue adds no external dependencies: SQLite, CSV,
timestamps, and UUIDs use Python's standard library. The pinned direct packages
were imported again and exercised by offline lifecycle and Streamlit UI checks.
Streamlit widgets use the supported `width="stretch"` API.

Target environment: **Python 3.12 on Linux**.

| Direct package | Pinned version | Verification |
| --- | --- | --- |
| Streamlit | 1.64.0 | Current PyPI metadata; released 2026-09-15 |
| CrewAI | 1.15.23 | Current PyPI metadata; released 2026-09-28 |
| Groq Python SDK | 1.7.0 | Current PyPI metadata; released 2026-08-26 |
| Pydantic | 2.12.5 | Latest non-prerelease below CrewAI's `<2.13` bound |

Pydantic 2.13.5 was the latest overall release at verification time but is
incompatible with CrewAI 1.15.23's declared dependency constraint. This project
uses 2.12.5 deliberately.

The complete dependency set was successfully resolved and installed together
in a clean Python 3.12 environment. `requirements.lock.txt` contains that
resolved set; `requirements.in` records the four direct dependencies.

The project uses the documented custom `BaseLLM` interface with the Groq SDK.
It does not need the `crewai[litellm]` extra. The standard native CrewAI Groq
provider route uses LiteLLM, but this bounded adapter intentionally calls the
SDK directly to control actual requests and retries.

Primary references checked:

- https://pypi.org/project/crewai/1.15.23/
- https://pypi.org/project/streamlit/1.64.0/
- https://pypi.org/project/groq/1.7.0/
- https://pypi.org/project/pydantic/2.12.5/
- https://docs.crewai.com/en/learn/custom-llm
- https://docs.crewai.com/en/concepts/flows
- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/models
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management

Model availability and account rate limits can change independently of Python
package versions. `openai/gpt-oss-20b` was listed as supporting strict JSON
outputs when checked. No real Groq API request was made during verification.
