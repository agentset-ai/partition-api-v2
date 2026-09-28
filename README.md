<h3 align="center">Agentset - Partition API V2</h3>

<br/>

This is the partition API used by the [Agentset Platform](https://github.com/agentset-ai/agentset).

## Tech Stack

- [Modal](https://modal.com/) – deployments
- [Marker](https://github.com/datalab-to/marker) – document parsing
- [Chonkie](https://github.com/chonkie-inc/chonkie) chunking
- [FastAPI](https://fastapi.tiangolo.com/) – API

## Deployment

1. Install dependencies:

```bash
uv sync
```

2. Create your modal API token (Manage Workspaces -> API Tokens)

3. Link modal cli to your account (copy the commands from the modal dashboard and run them with `uv run`):

```bash
uv run modal token set --token-id ak-xxx --token-secret as-xxx --profile=example
```

```bash
uv run modal profile activate example
```

4. Create secrets (read `.env.example` for more info about the variables):

```bash
# API key to secure the API
uv run modal secret create --force partitioner-secrets AGENTSET_API_KEY=xxx

# Redis host, port, and password
uv run modal secret create --force partitioner-secrets REDIS_HOST=xxx REDIS_PORT=xxx REDIS_PASSWORD=xxx

# Datalab API key
uv run modal secret create --force partitioner-secrets DATALAB_API_KEY=xxx

# R2 access key, secret key, bucket name, endpoint URL, and public URL
uv run modal secret create --force partitioner-secrets R2_ACCESS_KEY_ID=xxx R2_SECRET_ACCESS_KEY=xxx R2_BUCKET_NAME=xxx R2_ENDPOINT_URL=xxx R2_PUBLIC_URL=xxx
```

5. Deploy the app:

```bash
uv run modal deploy -m src.app
```

6. Done 🎉
   <br />

Now take the URL from the modal dashboard `PARTITION_API_URL` and the `AGENTSET_API_KEY` you specified above, and add them to the [Agentset Platform](https://github.com/agentset-ai/agentset) as `PARTITION_API_URL` and `PARTITION_API_KEY`, respectively.

They should look like this (`PARTITION_API_URL` is the base URL; the platform appends `/ingest`):

```bash
PARTITION_API_URL=https://example.modal.run
PARTITION_API_KEY=xxx
```

`FIRECRAWL_API_KEY`, `YOUTUBE_API_KEY`, `PROXY_USERNAME` and `PROXY_PASSWORD` are optional; they're only needed for crawl and YouTube ingestion.

## EU deployment

The EU app (`agentset-ingest-eu`) is deployed with `AGENTSET_REGION=eu` to its own Modal environment, `eu`, which has its own `partitioner-secrets`. All of its functions run in the Modal region `eu`, and the web endpoint routes requests through `eu-west`.

On EU the service:

- keeps each ingest and crawl request in Redis (`job:<id>`, expires after 3 hours) and passes only the job id to the worker, which deletes the record once the job has run
- refuses ingest and crawl jobs (503) unless `R2_ENDPOINT_URL` is an EU jurisdiction R2 endpoint, `REDIS_HOST` is set and `DATALAB_PROCESSING_LOCATION` is `eu`
- sets a 3-day expiry on the chunk batches it writes to Redis
- uploads documents to Datalab's EU storage with the File Upload API, converts them with `processing_location=eu`, downloads the result from the signed `result_url` and then deletes the file from Datalab; files over 200 MB are rejected
- returns 403 from `/youtube`, and 404 from the `/ingest`, `/crawl` and `/youtube` results endpoints (`…/results/{call_id}`)
- leaves the request input out of validation error (422) responses
- logs IDs and error types only, and returns generic error codes to the app
- leaves the filename out of the completion data sent to Trigger.dev
- uploads extracted images with `Cache-Control: no-store`

Modal stores function inputs over 2 MiB in `us-east`, so on EU the app sends text documents as a presigned URL to a `.txt` object instead of inline text. The service handles `text/plain` URLs exactly like inline text.

1. Create the `eu` environment in Modal (once).

2. Create the EU secret in that environment. Use the EU Redis, the EU jurisdiction R2 endpoint (`https://<account-id>.eu.r2.cloudflarestorage.com`) and EU buckets, set the Firecrawl key (required on EU) and leave the YouTube keys unset:

```bash
uv run modal secret create --force --env eu partitioner-secrets \
  AGENTSET_REGION=eu \
  DATALAB_PROCESSING_LOCATION=eu \
  AGENTSET_API_KEY=xxx \
  DATALAB_API_KEY=xxx \
  FIRECRAWL_API_KEY=xxx \
  REDIS_HOST=xxx REDIS_PORT=xxx REDIS_PASSWORD=xxx \
  R2_ACCESS_KEY_ID=xxx R2_SECRET_ACCESS_KEY=xxx R2_ENDPOINT_URL=xxx \
  R2_BUCKET_NAME=xxx R2_CHUNKS_BUCKET_NAME=xxx R2_PUBLIC_URL=xxx
```

`--force` replaces the whole secret, so always pass every key. `R2_PUBLIC_URL` is the public URL of the EU images bucket.

3. Deploy. `routing_region` needs `modal>=1.4.3`:

```bash
AGENTSET_REGION=eu MODAL_ENVIRONMENT=eu uv run --with "modal>=1.4.3" modal deploy -m src.app
```

The deploy stops before doing anything if `AGENTSET_REGION=eu` is set without `MODAL_ENVIRONMENT=eu` (or `--env eu`), if the Modal client is older than 1.4.3, or if a non-EU app targets the `eu` environment. A function's `routing_region` can only be set on its first deploy; changing it requires a new app.

4. Set `PARTITION_API_URL` (the `partition_api` URL in the `eu` environment, without `/ingest`) and `PARTITION_API_KEY` in the EU app deployment.

## Tests

```bash
uv run python -m unittest discover -t . -s tests
```

## License

This project is open-source under the MIT License. You can [find it here](https://github.com/agentset-ai/partition-api/blob/main/LICENSE.md).
