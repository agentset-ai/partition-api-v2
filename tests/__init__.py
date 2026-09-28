import os

# modules read the region at import time; tests import them as US and switch
# regions per test with patch.dict(os.environ, ...)
for name in ("AGENTSET_REGION", "MODAL_ENVIRONMENT", "DATALAB_PROCESSING_LOCATION"):
    os.environ.pop(name, None)
