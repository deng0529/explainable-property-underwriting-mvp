# Submission documents

`submissions/<case-id>/` contains the original supplied synthetic-case documents.
Paths are declared in `config/case_catalogue.json`, relative to the project root.
Adding another case requires catalogue metadata and its document; no Add Company UI
is implemented. Do not infer extracted fields from catalogue metadata.

The Northbridge PDF is unchanged from the supplied original:

SHA-256: `edc72d366268debdab35ba8151b9a5fdafe1476a3dc16051c874a36405f63c3f`.

Credentials, retained responses, database exports, logs and local caches do not
belong in this tracked directory. Turso remains the saved-result repository.
