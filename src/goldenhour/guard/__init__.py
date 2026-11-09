"""Guard: a deterministic policy co-signer for custody wallets.

Guard sits in front of a signing service, or holds one share of its key, and answers one question
for every request: should this be signed? It decodes the request into explicit actions, checks them
against a written policy, and returns allow, hold or deny with the reasons. A hold waits for a
quorum of human approvals. Every decision is written to a signed, hash-chained audit log.

The package does not import its submodules here, so `goldenhour --help` stays fast.
"""
