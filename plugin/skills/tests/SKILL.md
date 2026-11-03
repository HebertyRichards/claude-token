---
name: tests
description: Writes tests for a feature or a bug in the project's own test runner (Vitest, Jest, pytest, PHPUnit, Pest, Go, JUnit with Spring Boot, Quarkus or Micronaut, xUnit/NUnit with ASP.NET Core, RSpec, Playwright, Cypress and others) - unit, integration, API and end-to-end - deriving each case from a business rule or a reproduction, proving every new test can fail, sets up or fixes the CI workflow (GitHub Actions) that runs them, and writes the smoke tests that gate a deploy. Use when asked to write, add or generate tests, raise coverage, cover a feature or a bug with tests, fix a flaky test, review a test suite, or create or adjust a CI pipeline, without changing production code.
---

# Tests

A test is worth keeping when it checks a rule someone cares about and you have seen it fail. Green on the first run proves only that it runs.

## 1. The project's rules come first

Read before writing. These win over this skill:

- `AGENTS.md` / `CLAUDE.md`: the real test, lint and type-check commands. Use those; do not invent others.
- `CONTEXT.md` and `docs/adr/`: business rules are where the cases come from.
- The runner already configured (`vitest.config.*`, `jest.config.*`, `playwright.config.*`, `pytest.ini`/`pyproject.toml`, `phpunit.xml`) and the existing tests: copy their folder, naming, fixtures and helpers. No runner yet? Propose one that fits the stack and ask before installing it.
- `.claude/settings.json`: denied commands and guard hooks. Never work around them.

**Find the code with `claude-token search <words from the task>` before any grep or Glob**: it returns the `file:start-end` ranges to read, and builds the project's index on first use. `[claude-token] RAG` pointers already in the prompt come from the same search: read those first. Outside git, run `claude-token index <project folder>` once; without `claude-token`, use `grep -rn`.

## 2. Pick the mode

| The user said | Mode | What you may touch |
|---|---|---|
| write, add, cria, gera testes, cobre | **Write** | test files, fixtures, test config |
| review, audit, analisa os testes, "não modifique" | **Audit only** | nothing: report findings |
| CI, pipeline, actions, workflow | **CI** | `.github/workflows/` and the scripts it calls |

This skill does not change production code. A new test that exposes a bug is a finding: report it with the failing line and leave the fix to the bugfix work, unless the user asks for both.

## 3. Where the cases come from

- **From a feature**: list its rules first, numbered, from the request, `CONTEXT.md` or the code. Each rule gets at least one test, and the test name says the rule (`rejects an order above the credit limit`).
- **From a bug**: the exact input that broke, as a test that fails on the broken code. Then its neighbours: the same path with the value just below and just above.
- **For every case list**, ask which of these apply and write the ones that do:
  - the normal path;
  - limits: empty, zero, one, maximum, the value just past it;
  - wrong input: missing field, wrong type, too long;
  - permission: another user's id answers 404, another tenant's data never shows, no session answers 401;
  - failure outside: timeout or error from the database or an external API.

Stop and ask before writing when the rule is ambiguous, a limit needs domain knowledge (the maximum allowed amount), or the plan passes 30 tests: show the list and let the user pick.

## 4. The runner

Use the one the project already has; the config file tells which. Run one file quietly first:

| Stack | Runner (config) | One file, quiet |
|---|---|---|
| JS/TS | Vitest (`vitest.config.*`) | `vitest run <file> --reporter=dot` |
| JS/TS | Jest (`jest.config.*`) | `jest <file> --silent` |
| JS/TS | Mocha (`.mocharc*`) / `node:test` | `mocha <file> --reporter dot` / `node --test <file>` |
| Python | pytest (`pytest.ini`, `pyproject.toml`) | `pytest <file> -q --tb=short -x` |
| Python | unittest | `python -m unittest -q <module>` |
| PHP | PHPUnit (`phpunit.xml`) / Pest | `phpunit <file> --no-progress` / `pest <file> --compact` |
| Go | `go test` | `go test ./<pkg> -run <Name> -count=1` |
| Java/Kotlin | JUnit 5 via Maven (Surefire, Failsafe for `*IT`) | `mvn -q test -Dtest=<Name>` / `mvn -q verify -Dit.test=<NameIT>` |
| Java/Kotlin | JUnit 5 via Gradle | `gradle test --tests <Name> -q` |
| Ruby | RSpec / Minitest | `rspec <file> --format progress` / `bin/rails test <file>` |
| C#/.NET | xUnit, NUnit or MSTest | `dotnet test --filter "FullyQualifiedName~<Name>" --verbosity quiet` |
| Rust | `cargo test` | `cargo test <name> -q` |
| Browser | Playwright (`playwright.config.*`) | `playwright test <file> --reporter=line` |
| Browser | Cypress (`cypress.config.*`) | `cypress run --spec <file> --quiet` |

API and integration tests, in the framework's own way:

| Framework | HTTP layer | Database | Replace a dependency | Logged-in user |
|---|---|---|---|---|
| Go (`net/http`, Gin, Echo, chi) | `httptest.NewRecorder` on the handler, `httptest.NewServer` for a client | Testcontainers for Go, or a test database per package | an interface with a fake; no mock library unless the project has one | build the request with the session cookie or token |
| Spring Boot | `@WebMvcTest` + MockMvc for one controller; `@SpringBootTest(webEnvironment = RANDOM_PORT)` + `WebTestClient` or `TestRestTemplate` for the whole app | `@DataJpaTest` for repositories; Testcontainers with `@ServiceConnection` for the real database | `@MockitoBean` (`@MockBean` before Boot 3.4) | `@WithMockUser` or a real token from a test issuer |
| Quarkus | `@QuarkusTest` + REST Assured (`given().when().get(...).then().statusCode(...)`); `@QuarkusIntegrationTest` for the built artifact | Dev Services start the database container by themselves | `@InjectMock` | `@TestSecurity(user = ..., roles = ...)` |
| Micronaut | `@MicronautTest` + `@Client("/") HttpClient` | Test Resources or Testcontainers | `@MockBean` (Micronaut's) | a signed test token |
| ASP.NET Core | `WebApplicationFactory<Program>` + `CreateClient()` | Testcontainers for .NET, or SQLite; not EF Core InMemory for relational rules (no constraints, no transactions) | `WithWebHostBuilder(b => b.ConfigureTestServices(...))`; Moq or NSubstitute | a test authentication handler registered in `ConfigureTestServices` |
| Node (Express, Fastify, NestJS) | Supertest; `fastify.inject`; NestJS `Test.createTestingModule` | Testcontainers or a test database | the framework's provider override | a token signed with the test key |
| Python (FastAPI, Django, Flask) | `TestClient` / `httpx`; Django `APIClient`; Flask `test_client()` | a test database per run, rolled back per test | `app.dependency_overrides` (FastAPI) | `force_authenticate` (DRF) or a real login call |
| PHP (Laravel, Symfony) | `$this->getJson(...)`; Symfony `WebTestCase` | `RefreshDatabase` / a test database | `$this->mock(...)` / the test container | `actingAs($user)` / `loginUser($user)` |
| Rails | request specs | transactional fixtures, FactoryBot | dependency injection or `instance_double` | `sign_in` from the auth library's test helpers |

Unit tests per language: Go table-driven with `t.Run` and `-race`; JUnit 5 `@ParameterizedTest`, AssertJ and Mockito, or Kotest and MockK in Kotlin; xUnit `[Theory]` + `[InlineData]` (NUnit `[TestCase]`, MSTest `[DataRow]`). Components: Testing Library on the project's runner, or Cypress component tests. Parsers and data transforms with many inputs: a property-based test (fast-check, Hypothesis, jqwik, FsCheck, Go fuzzing) beats a long list of examples.

## 5. How to write them

- Pick the cheapest level that proves the rule: a pure function gets a unit test; a route gets an API test against a real test database; only a flow the user clicks through gets end-to-end.
- Assert what a caller sees: the response, the saved row, the text on screen. Not how many times a mock was called.
- Mock only the edges: network, clock, randomness, e-mail. Never the code under test.
- Each test builds its own data (factory or fixture) and cleans up. No order between tests, no shared mutable state, fixed clock and seed.
- Test data is made up. Never a real person's data, a production copy or a real secret; keys come from the test environment.
- End-to-end: find elements by role, then label, then text, test id last. Wait on a condition, never a fixed time. Navigate relative to `baseURL`. Log in once per worker, not per test.
  - Playwright: `getByRole`, `expect(locator).toBeVisible()`, `storageState` for the login; no `waitForTimeout`.
  - Cypress: `cy.findByRole` (Testing Library) or `cy.contains`, `cy.intercept` with an alias and `cy.wait('@alias')`, `cy.session` for the login; no `cy.wait(<ms>)`.
- Flaky test: find what varies (time, order, network, animation) and pin it. Retries hide it; they are not a fix.

## 6. Prove each new test

1. Run only the new test file, quietly. It must pass, or fail for the bug it was written for.
2. Break it on purpose once: change the expected value, or comment out the line of production code it guards, and run it again. It must go red. Undo the change and check that the diff holds only test files.
3. A test that stayed green is checking nothing: fix the assertion or delete it.
4. Run the project's whole suite and type check once at the end.

## 7. CI (GitHub Actions)

- Read what exists in `.github/workflows/` first and change the smallest part. One job for the checks the project already runs locally: install, lint, type check, test, build, in that order.
- Install from the lockfile: `pnpm install --frozen-lockfile`, `npm ci`, `composer install`, `pip install -r` with pinned versions, `go mod download`, `dotnet restore --locked-mode`, the Maven or Gradle wrapper (`./mvnw`, `./gradlew`). Runtime version from `.nvmrc`, `engines`, `packageManager`, `composer.json`, `go.mod`, `global.json`, or the Java release in `pom.xml` / the Gradle toolchain, not a guess.
- Testcontainers and Quarkus Dev Services need Docker: GitHub's `ubuntu-latest` runners have it; Windows and macOS runners do not.
- Every workflow sets `permissions: contents: read` (raise it per job only when needed), `timeout-minutes`, and `concurrency` with `cancel-in-progress` for pull requests. Cache through the setup action.
- Third-party actions pinned to a full commit SHA with the version in a comment; `actions/*` may use the major tag.
- Database or Redis for tests run as `services:` with throwaway credentials written in the workflow. Real secrets only as `${{ secrets.NAME }}`, never echoed, never in a pull request from a fork. No `pull_request_target` that checks out the PR's code.
- Dependency updates: Dependabot (`.github/dependabot.yml`) or Renovate, grouped by ecosystem, weekly, so each update arrives as a pull request that this CI checks.
- Check the YAML with `actionlint` if installed. Say plainly that the workflow has not run until it runs on GitHub.

## 8. Validate the deploy

The deploy itself belongs to the cd work, and nobody here runs it. This skill decides when a deploy is allowed and whether it worked:

- **Before**: the CI job is a required status check on the main branch, so a red build cannot reach a deploy.
- **After**: smoke tests the pipeline runs against staging and right after production. A few seconds, read-only, with a test account: the health endpoint, the login, one main page or read path, the version endpoint showing the new commit. They never write real data or touch real users' records.
- Base URL and test credentials come from the pipeline's environment, never from the test file. A failing smoke test fails the deploy job, so the pipeline can roll back.

## Spend few tokens

- The quiet commands from section 4: one file first, the suite once at the end. Browser runs: `--reporter=line` or `--quiet`, and no videos or screenshots unless a test failed.
- Long output to a file, then `tail -50` or `grep -n` the failure.
- Read the code under test by range; read one existing test as the pattern, not the whole folder.
- Coverage reports: read only the uncovered lines of the files in scope, never the whole report.

## Report

```
Cases:  the rule list, each with its test name             (Write)
Proof:  the command, its result line, and the red line seen when broken on purpose
Found:  file:line of any bug a new test exposed, not fixed
CI:     the workflow changed, what it runs, and that it has not run yet  (CI)
Gate:   the required checks and the smoke tests for the deploy, if any
Left:   what was not covered and why
```

Audit only: one line per finding, worst first: `file:line: problem → fix`, then the rules with no test at all.

## Never

- Weaken or delete an assertion to make a test pass.
- Change production code in this skill without the user asking.
- Leave `.only`, `.skip`, `xit`, `print`, `console.log` or temporary files behind.
- Put a secret, token or personal data in a test, a fixture, a snapshot or a workflow.
