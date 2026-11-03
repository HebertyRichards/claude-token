---
name: frontend
description: Builds, fixes and reviews frontend layout inside the project's own design system - its tokens, components, fonts and i18n - mobile first, accessible to WCAG AA, checked by looking at the rendered page, with lean token use. Use when creating or changing a page, screen, component or layout, when something looks broken or misaligned on some screen size, or when asked to audit a layout or UI without changing it.
---

# Frontend

Every project has its own look and rules. Your job is to extend that look, not to bring yours.

## 1. The project's design comes first

Find what already decides the look, and follow it. It wins over this skill:

- `AGENTS.md` / `CLAUDE.md`, `DESIGN.md`, `PRODUCT.md`, `CONTEXT.md`.
- Tokens: `tailwind.config.*` or the `@theme` block in the global CSS, CSS custom properties, `components.json` (shadcn/ui), the theme file.
- Existing components (`src/components/ui`, `components/`): reuse a button, card, dialog or form field before writing one.
- i18n: if the project has `messages/`, `locales/` or `i18n`, every visible string goes through it, in every locale file.
- Lint and format: `biome.json`, `.eslintrc`, `.prettierrc`; run the project's own command.
- `.claude/settings.json`: denied commands and guard hooks. Never work around them.

A refinement keeps the colours, fonts, spacing scale, copy and behaviour it found. A new palette, font or component library is a redesign: ask first.

**Find the code with `claude-token search <words from the task>` before any grep or Glob**: it returns the `file:start-end` ranges to read, and builds the project's index on first use. `[claude-token] RAG` pointers already in the prompt come from the same search: read those first. Outside git, run `claude-token index <project folder>` once; without `claude-token`, use `grep -rn`.

## 2. Pick the mode

| The user said | Mode | What you may touch |
|---|---|---|
| build, create, fix, ajusta, arruma | **Build** | the files the screen needs |
| audit, review, analyze, analisa, "não modifique" | **Audit only** | nothing: report findings |

What the screen is for sets the bar: a dashboard or admin panel (**Operate**) wants scanability, consistency and density, not decoration. A landing page (**Persuade**) earns attention and one clear action.

## 3. Rules every screen meets

| Area | Do | Avoid |
|---|---|---|
| Breakpoints | phone styles as the base, `min-width` (`sm:`/`md:`/`lg:`) adds layout | `max-width` queries that shrink a desktop layout |
| Width | fluid containers, grid/flex, `min-w-0` on flex children that hold text | fixed widths above 360px, horizontal scroll |
| Tables | wrapper with `overflow-x-auto`, or cards on phones | a table wider than the screen |
| Text | body 16px, readable line length, truncation with a `title` or tooltip | text under 12px, clipped text with no way to read it |
| Touch | targets at least 44-48px, spaced apart | icon buttons smaller than a finger |
| Forms | visible `<label>`, inputs 16px+ (no iOS zoom), right `type`/`autocomplete`, error text tied with `aria-describedby` | placeholder as the only label |
| Contrast | WCAG AA: 4.5:1 text, 3:1 large text and UI parts, both themes if there is dark mode | grey-on-grey, colour as the only signal |
| Keyboard | visible focus, logical tab order, dialogs trap and return focus, Esc closes | `outline: none` with nothing in its place |
| Images | `width`/`height` or the framework's image component, lazy below the fold, real `alt` | layout shift, full-size photos on phones |
| States | loading, empty, error and long-content states for every list and form | only the happy path |
| Look | the project's tokens and one accent | default purple gradients, emoji headings, filler copy, cards inside cards |

## 4. Check it by looking

Code that compiles is not a layout that works.

1. Run the project's lint and type check, quietly.
2. Render it and look at **360px** and **1280px** (and 768px if the layout changes there): no horizontal scroll, nothing overlapping, nothing clipped, text readable without zoom.
3. Batch the fixes from one look before looking again. Each screenshot costs ~1.1-1.6k tokens: do not re-capture what you already saw.
4. Inspect the DOM through selectors and small snippets, never a full-page HTML dump.

No way to render (no dev server, no browser tool)? Say so, and label layout findings as **Inferred from code**.

## 5. Report

Audit only, one line per finding, most severe first:

```
[high|medium|low] src/components/x.tsx:42 (360px): what breaks, for whom. Evidence: class/rule or screenshot. Fix: one line
```

Build mode: what changed, the sizes you looked at, lint and type check results, and anything not verified.

## Spend few tokens

- Find components with `grep -rn` / Glob, read them by range.
- Never read `node_modules`, build output (`.next/`, `dist/`) or compiled CSS.
- Do not paste whole config files back; quote the line that matters.
