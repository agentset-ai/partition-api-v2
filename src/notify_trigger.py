from fastapi.responses import JSONResponse
import os
import requests
from .region import is_eu

# this method will notify the trigger.dev workflow that the ingest operation has completed
def notify_workflow(status: int, body: dict, trigger_token_id: str, trigger_access_token: str):
  content = {"status": status}
  content.update(body)

  r = requests.post(
      f"https://api.trigger.dev/api/v1/waitpoints/tokens/{trigger_token_id}/complete",
      headers={"Authorization": f"Bearer {trigger_access_token}"},
      json={"data": content}
  )
  r.raise_for_status()

  # on EU the function's return value only carries the status
  if is_eu():
    return {"status": status}

  return JSONResponse(
      status_code=status,
      content=content,
  )
