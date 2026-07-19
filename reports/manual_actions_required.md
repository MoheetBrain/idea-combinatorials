# Manual actions required

## Foursquare category taxonomy

No HF_TOKEN or authenticated Hugging Face CLI session was detected.

The dataset is gated. No bypass, mirror, substitute, or placeholder was used.

1. Sign in at https://huggingface.co/datasets/foursquare/fsq-os-places.
2. Review and accept the repository's current gated-access terms.
3. Create a read-only Hugging Face access token at https://huggingface.co/settings/tokens.
4. Authenticate locally with `hf auth login` (or `huggingface-cli login` on older clients).
5. Rerun `python scripts/01_download_sources.py --skip-existing`.

Do not paste a token into a report, shell history, source file, or command argument. `HF_TOKEN` may instead be supplied through a secure environment mechanism.

Alternatively, create an account at https://places.foursquare.com and follow the portal's generated Iceberg connection instructions. The repository script uses the Hugging Face route because it can enumerate and select only the latest category Parquet files.
