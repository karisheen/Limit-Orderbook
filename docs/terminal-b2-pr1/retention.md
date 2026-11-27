# Evidence retention recommendation — PR2–PR7

This is a publication recommendation, not new policy tooling, CI, a runtime change, or authorization to begin PR2. Apply it separately to each authorized milestone.

## Keep a small, auditable public packet

- **Summary:** scope, source/review identities, decisions, limitations and actual outcomes. Preserve complete independent reports when publishing them; distinguish editorial context and later owner decisions from the reviewers' words. Mark omitted historical evidence citations **LOCAL-ONLY** rather than creating broken public links.
- **Screens:** representative original-resolution native captures with source, instrument, viewport, DPR, theme/view, delivery/state and collection provenance. Retain matched before/after comparisons and images needed to establish an important correction, not every duplicate matrix or preview. Record byte counts and hashes in a compact public selection manifest; identify it as a new summary, not the original collector output.
- **Corrections:** retain real failed outcomes, a useful failure image or sanitized excerpt, the repair and its bounded closure evidence. Distinguish intermediate failure from final appearance. Do not replace a negative with a success-only narrative.
- **Final receipts:** compact, privacy-reviewed records of commands, execution scope, code identity, counts, skips, failures and final disposition. Separate historical receipts from checks actually run on the publication head. Never turn curation, an owner approval or a focused rerun into a new independent review or a full-suite pass.

Prefer reproducible offline native sources and representative imagery over live accounts or authored scenes. Label illustrative specimens, response interception, representation padding and other deliberate exceptions at the point of use; they do not establish unmodified native-market behavior. Scope all measurements: sampled contrast is not accessibility certification, screenshot dimensions are not physical-display readability, fixture lag is not performance, and one theme/view/viewport is not a cross-product result.

## PR1 selection and exclusions

The historical `docs/terminal-b2-pr1/evidence/` subtree contained 120 files / 22,544,201 bytes (21.50 MiB), including 10.98 MiB of failed and superseded attempts. The curated subtree contains **28 files / 5,935,138 bytes (5.66 MiB)**. Both counts exclude the surrounding Markdown and source code; the original review packet as a whole contained 129 files.

PR1 retains 20 selected final-matrix images (four before, sixteen after), two ancillary native inspection images and the unchanged failed clipped-bid PNG: see [captures.json](evidence/captures.json). The ancillary observations predate the final focus/contrast corrections and are not substitutes for the final matrix. It also retains targeted contrast data, compact gate/failure records and both complete independent reviews. [Verification](verification.md) distinguishes prior results, failed attempts and publication-only checking.

Excluded from public PR1: redundant matrix images, superseded candidate matrices, historical design/prototype files, convenience crops/comparisons, most ancillary inspection images, original verbose manifests and raw request/response inspection records, full logs (including compressed logs), traces and attempt archives. These are either redundant, outside the representative claim, or carry unnecessary local-path/session/metadata exposure. Historical textual provenance references remain in the public documents, explicitly labelled LOCAL-ONLY where their targets are omitted. Exclusion does not negate their historical outcomes or make the original reviewers' wider evidence publicly reproducible.

The privacy inspection identified nine historical files requiring path normalization. All nine were normalized privately: 21 substitutions across 18 lines, preserving source locations, values and outcomes. For the compressed log this included decoding the payload and removing the per-run header name and timestamp on recompression. The original-to-derivative mapping, its hashes and the complete derivatives remain private. Of those nine files, only a [labelled failure excerpt](evidence/regressions/clipped-touch.txt) is represented directly in the public candidate. None of the nine original blobs is included. The Git ancestry and per-path changes are documented in [equivalence](equivalence.md).

## Preserve private originals separately

Keep the immutable originals, complete matrices, raw receipts, attempts and archives in access-controlled private storage. Maintain a private mapping from each public artifact to its original Git object/path, hash and any transformation or redaction. Do not overwrite originals or publish the mapping, private archive or private provenance refs as public dependencies. Sanitized derivatives must be labelled as derivatives; retained original images must remain byte-identical. Restrict access and set retention/deletion decisions according to sensitivity and reproducibility needs, rather than keeping raw authentication or session material indefinitely by default.

## Exact-head pre-push hygiene

Before each publication, the integration owner should:

1. Inspect the proposed public files **and every newly reachable commit/object**, not just the tip diff. Review commit messages and metadata trails as well as content. Keep private evidence ancestors and refs outside the public ancestry; a later deletion/cleanup commit does not conceal earlier bytes.
2. Inspect compressed/archive contents, not merely filenames or a raw-byte search. For gzip inspect header filename, comment, timestamp and extra fields **and** decompressed body; inspect nested archive paths/content and image EXIF/XMP/comments where present. Check logs, JSON, traces, screenshots and manifests for local login/checkout/temp paths, credentials, authorization headers, cookies, tokens, authentication/session IDs and identifying request URLs. Use Git-relative paths or `$REPO`, `$HOME`, `$TMPDIR` in public explanations, not real local paths.
3. Remove unnecessary private material from the proposed publication; derive the minimum sanitized public excerpt while preserving its real result and private original. Document transformations without disclosing the removed values. Do not assume an archive, hash, ignored file or cleanup commit makes exposed secrets safe.
4. Review the exact final public head SHA, intended remote/base and complete reachable publication delta. Obtain owner approval for that SHA immediately before pushing only the intended public ref. Any subsequent change needs renewed exact-head review/approval; do not push private branches, tags or archives incidentally.

No history rewrite is proposed or performed by this recommendation. If sensitive material has already been published, treat it as exposure: revoke/rotate affected credentials and seek authorized repository/hosting remediation. Adding a cleanup commit is not secret removal, and neither rewriting history nor deleting a file can promise that prior copies disappeared.
