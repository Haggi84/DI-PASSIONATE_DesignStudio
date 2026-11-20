# Phase 0 — Who is Package Studio for?

Goal: decide, with the people who would use it, what the tool must do — and
what it leaves to other tools. Two weeks, before more is built.

## Hypothesis to test

> IHP SG13G2 users (universities, start-ups, research groups) have no open
> way to answer *"does my chip still work in its package, and which package
> do I take?"* — and need a bonding diagram the assembly house accepts.
> Package Studio closes that gap: GDS → package → board cut-out → EM →
> Touchstone → back into xschem/ngspice, checked against the pin list.

What would falsify it: users who package via the assembly house's own
design service and never simulate the package; users above ~10 GHz who need
flip-chip and not wire bonds; users who already use a commercial flow.

## Who to talk to (5–8 conversations, 45 min each)

| Who | Why |
|---|---|
| FAU LeSoC (System-on-Chip chair) | design chips, must package and test them |
| 2–3 IHP open-MPW user groups (IHP-Open-DesignLib authors) | the target user exactly |
| a packaging / assembly service (e.g. IHP's partners) | what a bonding diagram must contain |
| 1–2 FAPS doctoral students (packaging, MID) | internal users, MID substrates |
| an RF lab that measures packaged parts | what "validated" has to mean |

## Interview guide

1. Walk me through your last chip from tape-out to a measured part. Where
   did it take longest? Where did it go wrong?
2. How did you choose the package? Who drew the bonding diagram, with what?
3. Did you simulate the package? If not, why not? If so, with what, and did
   it agree with the measurement?
4. Which simulator do you design in (xschem/ngspice, Qucs-S, Cadence…)? What
   would a package model have to look like to use it there?
5. Which board tool? Would you import the board from it, or draw a test
   board?
6. What does an assembly house ask you for? (Show our bonding diagram and
   wire list: what is missing?)
7. Frequency range, pin count, number of dies? MCM: same package, or
   substrate / flip-chip?
8. What would make you stop using Package Studio after the first try?

Show, do not tell: run `examples/lna_qfn40_on_board.py` live, then ask.

## One-pager (fill in after the conversations)

- **Users:** …
- **Their three jobs:** 1 … 2 … 3 …
- **What we do that nothing open does:** …
- **What we do not do (and point to instead):** board layout (KiCad), full
  system CAD (STEP from the vendor), chip design (xschem / KLayout / ORFS)
- **Measures of success:**

| Measure | Target | How measured |
|---|---|---|
| GDS to package S-parameters | < 1 working day | timed with a new user |
| simulated vs measured |S21| | ≤ 1 dB to 10 GHz | `tools/ps_validate.py` |
| bonding diagram accepted by an assembly house | first time | one real order |
| external users | ≥ 3 groups in 12 months | issues / mails |
