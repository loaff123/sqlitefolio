# Independent packet boundary qualification

All cases use newly authored, benign, local sample SQL. No downloaded SQL, live
source, network, extension, hostile parser input, or recovery experiment is used.

The following tests distinguish evidence consistency from source-bound rerun.
They must not weaken the protocol into a provenance/authenticity claim.

1. **Raw facts with stale summary:** append a valid typed row to observed state,
   leaving summary/report unchanged. Verify must return tamper/mismatch (6).
2. **Derived summary tamper:** change a projected row delta without changing raw
   facts, and regenerate deterministic HTML. Verify must still reject (6), proving
   that report agreement alone cannot substitute for independent reconstruction.
3. **Retained SQL byte tamper:** change retained migration SQL without updating its
   file digest. Verify must reject (6), including a semantic no-op comment change.
4. **Whole consistent synthetic observation:** change observed and reopened rows,
   independently reconstruct every derived summary field, and regenerate report.
   With unchanged source baseline and internally consistent shapes, structural
   verification may return 0. Replay against the explicit original manifest must
   return 6. This is expected trust-boundary behavior, not a verifier defect.
5. **Rehashed migration substitution:** change retained candidate SQL and refresh
   its file digest. Consistent structural checks alone cannot authenticate the
   former SQL. Replay against original exact SQL bytes must reject (6).
6. **Wrong original:** replay against a valid manifest whose schema, seed,
   migration, profile, or contract differs. Reject (6), even if a finite after-state
   happens to coincide. Input/source identity is part of the replay binding.
7. **Incomplete but coherent:** obtain a real resource-limited packet with a
   lowered one-row ceiling and two-row sample. Verify may call it structurally
   consistent, but must remain nonpassing/nonzero (4), with no invented row loss.
8. **Native error and open transaction:** unchanged structurally consistent packets
   preserve execution error/noncommitted state and their nonzero exit; replay
   equality is not a claim the candidate passed.

Shape-only rejection and static prose searches are insufficient evidence. Each
implemented test uses real CLI-produced packets and checks externally visible
exit codes, raw state, reconstructed summaries, and source bytes.
