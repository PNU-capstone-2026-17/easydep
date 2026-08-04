# Requirements-agent evaluation suite

This suite evaluates the requirements agent within EasyDep's Docker-on-VM scope.
Application inputs contain only requirements and cloud constraints. Expected facts live in
`oracle.json` and must never be included in an agent prompt.

## Anti-overfitting protocol

1. Use only the `development` split while changing prompts, schemas, or repair logic.
2. Do not copy application names, actors, or requirement sentences into prompts or rules.
3. Freeze `holdout` inputs and their SHA-256 hashes before optimization.
4. Run the holdout split only after a change is selected using development results.
5. Report macro averages across applications and every per-application result. Never select a
   change because it improves only one application.
6. A new failure mode requires a new development case; do not edit a holdout case after seeing
   its output.

The oracle contains only facts that are explicit in the requirements. It is used for evaluation,
not as a complete prescribed use-case model.

## Run the suite

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split development
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split holdout
.\.venv\Scripts\python.exe -m evaluation.requirements.run_suite --split domainExpansion
```

The holdout command verifies frozen hashes before making any LLM call. Domain-expansion inputs
are registered in `suite.json`; copy the files in `templates/` when adding one. See
`improvement-report.ko.md` for the completed improvement process and results.
