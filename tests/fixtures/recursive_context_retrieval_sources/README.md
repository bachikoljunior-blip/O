These files are the exact source bytes named by the existing three-fixture
retrieval ablation at commit `523f17119b5e32e0526c235bc52bb3db626fafa9`.
`SOURCE_PROVENANCE.json` retains every original repository path, Git blob,
SHA-256 digest and size. The original fixture manifest and report are unchanged.

The test fixture materializes these bytes under their original paths in a
temporary repository root. This lets the live observation ledger and other
current control sources evolve without changing historical test inputs. All
existing provenance, freshness, invalidation, authority and report assertions
still run. The production validator continues to reject changed source bytes.
Archived Python files are plain test data and are never imported or executed.
