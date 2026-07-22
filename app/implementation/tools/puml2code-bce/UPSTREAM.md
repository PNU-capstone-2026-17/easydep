# Upstream and EasyDep scope

This directory is an EasyDep-scoped fork of
[`jupe/puml2code`](https://github.com/jupe/puml2code), imported from upstream
commit `08a1f51ba8939a897fbc25d615927d557dac9154` under the MIT license.

EasyDep-specific changes:

- parse `class Name <<Boundary|Control|Gateway|Entity|Actor>> { ... }`;
- parse `fieldName: Type` and `method(arg: Type): ReturnType`;
- preserve nested Java generic types;
- ignore EasyDep rendering directives;
- skip Actor code generation;
- generate Boundary, Control, and outbound Gateway interfaces plus executable Entity models;
- support a Java base package and nested output directories.

Only the Java runtime path is retained. Upstream CI, issue templates, examples,
multi-language templates, tests, lint configuration, images, dev dependencies,
and Snyk tooling are intentionally excluded. Production npm dependencies are
lockfile-pinned, installed with `--omit=dev`, and audited by the bootstrap flow.
