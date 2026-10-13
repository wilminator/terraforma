"""What the account calls share: the strict body model and the rate-limit check."""

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from . import ratelimit


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


async def limited(request: Request, limit: ratelimit.Limit, value: str) -> None:
    try:
        await ratelimit.hit(request.app.state.sessionmaker, limit, value)
    except ratelimit.RateLimited as error:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, str(error), headers={"Retry-After": str(error.retry_after)}
        ) from error
