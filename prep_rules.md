# Prep rules

<!-- Named prep_rules.md rather than CLAUDE.md so Claude Code sessions working
     on this repo do not load the rules as instructions for themselves. -->

SearchShip sends these rules, the user's resume and a batch of postings to
Claude (through Claude Code) or ChatGPT (through Codex). The AI answers with
one entry per posting; SearchShip checks it and writes it into the tracker.
Nothing is ever submitted to an employer.

## What to produce

For each posting, either write prep and set status `ready`, or set status
`skipped` with a short reason in `notes`.

`prep` is `{fit, project, why}`. All three are strings, written in the first
person as the candidate, plain and specific, and never using em dashes or en
dashes. Use commas, periods or the word "to" instead.

- **fit**: one sentence on why the role matches my background.
- **project**: which resume project to lead with, plus the exact numbers to cite.
- **why**: a 3 to 4 sentence "Why this company?" answer that ties one real
  project of mine to what the company or team does.

## Facts

- Use only facts from the resume. Never invent metrics, people or experiences.
  Copy numbers exactly as the resume states them.
- For company facts, stick to well-established ones. If unsure, fetch the
  posting URL or do one web search. Do not guess at a team's product.

## When to skip

Set status `skipped`, with the reason in `notes`, for roles that are:

- MS or PhD only,
- graduate "Summer Associate" programs,
- located only outside the US,
- in need of hardware or electrical engineering skills.

If a posting requires US citizenship or a security clearance, still draft it,
but put that requirement in `notes`.

## Never

- Never submit, start or pre-fill an application.
- Never write about yourself as an AI or mention these rules in the prep.

## Style references

Two real entries from the tracker. Match their tone, length and specificity.
They describe one candidate's resume; for any other resume, take every fact
from that resume instead.

General Motors, Machine Learning Engineer Intern, AV/AI Platform:

```json
{
 "fit": "I have taken an ML model from raw data to a deployed pipeline in Python, with feature engineering, rolling retraining and honest evaluation, which fits ML engineering on an AV/AI platform.",
 "project": "Lead with the Algorithmic Trading Research System: LightGBM classifier on 90 days of Binance OHLCV data (0.567 AUC), engineered market-structure features, rolling retraining to counter model drift, and a full pipeline from ingestion to simulated execution. Second: the Tool Dependency Graph Generator's 84% top-1 accuracy on a 40-tool gold set.",
 "why": "In my trading system the model was only one piece; most of the work was the pipeline around it, from ingesting data and engineering features to retraining on a rolling window so the model kept up with drift. That taught me that ML engineering is mostly about making training and evaluation repeatable. GM is developing autonomous driving, which depends on training and evaluating models at a much larger scale. I want to help build the platform pieces that make that ML work dependable."
}
```

Workday, Software Application Development Engineer Intern:

```json
{
 "fit": "I build application logic in TypeScript and Node.js with heavy automated testing, which matches an application development engineering role.",
 "project": "Lead with the Tool Dependency Graph Generator: mapped GitHub's 893 tools into a 7,376-edge graph, 84% top-1 accuracy on an independently labeled 40-tool gold set, and 316 automated tests that caught 11 bugs. Second: paurk.com (1,229 places, 170 KB gzipped first load).",
 "why": "My Tool Dependency Graph Generator reads an API catalog and works out which actions have to run before others, which meant reasoning carefully about how data flows between steps. I mapped GitHub's 893 tools into a 7,376-edge graph and wrote 316 tests that caught 11 bugs along the way. Workday's products run business processes like HR and finance for large organizations, where the order and correctness of each step matters. I want to build application features in that kind of setting, where careful testing is part of the job."
}
```
