from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from multidict import CIMultiDict
from yarl import URL

from .model import CaptureStore, Transaction
from .decoding import CaptureDecoder
from .rewrite import rewrite_developer_roles


CLIENT_SESSION_KEY = web.AppKey("client_session", ClientSession)
CAPTURE_STORE_KEY = web.AppKey("capture_store", CaptureStore)
UPSTREAM_KEY = web.AppKey("upstream", URL)
CLAMP_DEVELOPER_ROLE_KEY = web.AppKey("clamp_developer_role", bool)

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass
class CaptureObserver:
    """Own the capture lifecycle while the proxy owns HTTP relay details."""

    transaction: Transaction
    store: CaptureStore
    decoder: CaptureDecoder | None

    @classmethod
    def start(
        cls,
        *,
        method: str,
        path_qs: str,
        body: bytes,
        store: CaptureStore,
    ) -> CaptureObserver:
        transaction = Transaction(
            method=method,
            path_qs=path_qs,
            request_body=body,
        )
        observer = cls(transaction, store, CaptureDecoder.start(transaction))
        store.add(transaction)
        return observer

    def set_response_status(self, status: int) -> None:
        self.transaction.response_status = status
        self.store.changed(self.transaction)

    def observe_chunk(self, chunk: bytes) -> None:
        self.transaction.append_response_chunk(chunk)
        if self.decoder is not None:
            self.decoder.consume_chunk(chunk)
        self.store.changed(self.transaction)

    def set_error(self, error: str) -> None:
        self.transaction.error = error
        self.store.changed(self.transaction)

    def set_forwarded_request(self, body: bytes, rewrite_count: int) -> None:
        self.transaction.forwarded_request_body = body
        self.transaction.developer_role_rewrite_count = rewrite_count
        self.store.changed(self.transaction)

    def finish(self) -> None:
        if self.decoder is not None:
            self.decoder.finish()
        self.store.complete(self.transaction)


def _connection_tokens(headers: Iterable[tuple[str, str]]) -> set[str]:
    tokens: set[str] = set()
    for name, value in headers:
        if name.lower() == "connection":
            tokens.update(token.strip().lower() for token in value.split(","))
    return tokens


def _forward_headers(
    headers: Iterable[tuple[str, str]],
    *,
    strip_host: bool = False,
) -> CIMultiDict[str]:
    header_items = list(headers)
    blocked = HOP_BY_HOP_HEADERS | _connection_tokens(header_items)
    if strip_host:
        blocked.add("host")
    return CIMultiDict(
        (name, value)
        for name, value in header_items
        if name.lower() not in blocked
    )


def _target_url(upstream: URL, request: web.Request) -> URL:
    return upstream.with_path(request.rel_url.path).with_query(
        request.rel_url.query_string
    )


async def _client_session_context(app: web.Application):
    timeout = ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=None)
    app[CLIENT_SESSION_KEY] = ClientSession(
        auto_decompress=False,
        timeout=timeout,
    )
    yield
    await app[CLIENT_SESSION_KEY].close()


async def proxy_request(request: web.Request) -> web.StreamResponse:
    store = request.app[CAPTURE_STORE_KEY]
    body = await request.read()
    capture = CaptureObserver.start(
        method=request.method,
        path_qs=request.path_qs,
        body=body,
        store=store,
    )
    forwarded_body = body
    rewrite_count = 0
    if request.app[CLAMP_DEVELOPER_ROLE_KEY]:
        forwarded_body, rewrite_count = rewrite_developer_roles(request.path_qs, body)
        if rewrite_count:
            capture.set_forwarded_request(forwarded_body, rewrite_count)

    session = request.app[CLIENT_SESSION_KEY]
    target = _target_url(request.app[UPSTREAM_KEY], request)
    request_headers = _forward_headers(request.headers.items(), strip_host=True)
    if rewrite_count:
        # aiohttp derives the new length from ``data``. Keeping the received
        # header would leave an upstream waiting for bytes from the old body.
        request_headers.popall("Content-Length", None)

    try:
        async with session.request(
            request.method,
            target,
            headers=request_headers,
            data=forwarded_body,
            allow_redirects=False,
        ) as upstream_response:
            capture.set_response_status(upstream_response.status)
            response_headers = _forward_headers(upstream_response.headers.items())
            response = web.StreamResponse(
                status=upstream_response.status,
                reason=upstream_response.reason,
                headers=response_headers,
            )
            await response.prepare(request)

            try:
                async for chunk in upstream_response.content.iter_any():
                    capture.observe_chunk(chunk)
                    await response.write(chunk)
            except (ConnectionError, RuntimeError) as exc:
                capture.set_error(f"downstream disconnected: {exc}")
                raise

            await response.write_eof()
            return response
    except (ClientError, TimeoutError) as exc:
        error = f"upstream request failed: {exc}"
        capture.set_error(error)
        raise web.HTTPBadGateway(text=error) from exc
    finally:
        capture.finish()


def create_app(
    upstream: str,
    *,
    capture_store: CaptureStore | None = None,
    clamp_developer_role: bool = False,
) -> web.Application:
    upstream_url = URL(upstream)
    if upstream_url.scheme not in {"http", "https"} or not upstream_url.host:
        raise ValueError("upstream must be an absolute http(s) URL")
    if upstream_url.path not in {"", "/"} or upstream_url.query_string:
        raise ValueError("upstream must not contain a path or query string")

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app[UPSTREAM_KEY] = upstream_url
    app[CAPTURE_STORE_KEY] = capture_store or CaptureStore()
    app[CLAMP_DEVELOPER_ROLE_KEY] = clamp_developer_role
    app.cleanup_ctx.append(_client_session_context)
    app.router.add_route("*", "/{path:.*}", proxy_request)
    return app
