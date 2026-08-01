# Local-Production Parity

Run the development-only checker against a local demo database:

```bash
MYSQL_DB=sentiment_demo \
EMBED_PROVIDER=local \
VECTORDB_BGE_DIR=on_the_porch/vectordb_bge_v1 \
python scripts/stage4_5_parity_check.py
```

The checker refuses non-loopback database hosts and database names outside the known Stage
4.5/demo set. It reports model names, embedding configuration, vector fingerprint, table
counts, category coverage, metadata fields, and redacted module hashes. It never prints
credentials.

A non-zero exit is expected when council-minute data or vectors are absent. That warning
does not block ordinary app startup. Full production parity must not be claimed from this
checker alone because public browser behavior does not reveal production commit, source
inventory, embeddings, or collection identity.
