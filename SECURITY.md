# Security

## Reporting

Open a [security advisory](https://github.com/cha-ndler/osrs-meta-lab/security/advisories/new)
rather than a public issue.

## Scope

This repository runs a **search over gear combinations** and scores them with a
vendored copy of the OSRS Wiki DPS calculator. It reads public JSON data and
writes markdown reports. It handles no credentials and no user data, and it
never contacts the game.

Output is experimental. It is a starting point for investigation, not advice.

## How the repository is protected

- `main` is protected: pull requests are required, force pushes and branch
  deletion are blocked.
- CI runs on every pull request.
- GitHub Actions are pinned to **commit SHAs**, not tags. A tag can be moved to
  point at new code by a compromised upstream account; a SHA cannot.
- The default workflow token is read-only, and CI checks out without persisting
  credentials.

## The vendored calculator

`vendor/osrs-dps-calc` is [`weirdgloop/osrs-dps-calc`](https://github.com/weirdgloop/osrs-dps-calc),
pinned to an exact commit. This matters for more than reproducibility:

- A submodule tracking a **branch** would let upstream change every DPS number
  here with no diff in this repository. CI asserts the entry is a 40-character
  commit, not a ref.
- Bumping it is a deliberate, reviewed change, because it invalidates previously
  published findings. Dependabot is not configured to touch it.
- CI also asserts no calculator source has been copied into `lab/` or `oracle/`.
  It is consumed as a dependency, unmodified, which is what keeps the GPL-3.0
  position clean and stops the vendored logic silently drifting from upstream.

Running the lab executes `npm install` in `oracle/` and a `yarn install` inside
the submodule. Those pull real dependency trees — run them somewhere you are
comfortable doing so.

## Branch protection settings

`main` requires a pull request with the `checks` job passing, and blocks force
pushes, branch deletion and non-linear history. Repository admins can currently
bypass these; see the note in the pull request template of the sibling
repository for how to tighten that.
