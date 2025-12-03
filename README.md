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

They should look like this:

```bash
PARTITION_API_URL=https://example.modal.run/ingest
PARTITION_API_KEY=xxx
```

## License

This project is open-source under the MIT License. You can [find it here](https://github.com/agentset-ai/partition-api/blob/main/LICENSE.md).
