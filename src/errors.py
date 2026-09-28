class PartitionError(Exception):
    """An error whose code and message are safe to log and return to the caller."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def eu_error_body(error: BaseException, code: str, message: str) -> dict:
    if isinstance(error, PartitionError):
        return {"message": error.message, "code": error.code}
    return {"message": message, "code": code}
