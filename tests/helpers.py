"""Small helpers the tests share."""


def expect(response, status: int):
    """The response, after checking its status: a failure says what the call was and what the server said."""
    assert response.status_code == status, (
        f"{response.request.method} {response.request.url.path} answered {response.status_code} "
        f"(expected {status}): {response.text}"
    )
    return response
