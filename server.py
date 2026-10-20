"""
Publishers MCP server: Springer Nature, Elsevier (Scopus), IEEE Xplore.
Run:  MCP_ACCESS_KEY=secret python server.py  ->  http://localhost:8000/mcp?key=secret
Keys are read from environment variables; tools for a publisher whose key
is missing return a clear error instead of crashing.
"""
import hmac
import os
import httpx
from starlette.responses import PlainTextResponse
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("publishers", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
TIMEOUT = 20


def _key(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing environment variable {name}")
    return v


@mcp.tool()
async def springer_search(query: str, max_results: int = 10) -> list[dict]:
    """Search Springer Nature (Meta API). Example query: 'machine learning' or 'doi:10.1007/...'."""
    q = query if ":" in query else f'"{query}"'
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(
            "https://api.springernature.com/meta/v2/json",
            params={"q": q, "p": max_results, "api_key": _key("SPRINGER_API_KEY")},
        )
        r.raise_for_status()
    return [
        {
            "title": x.get("title"),
            "authors": [a.get("creator") for a in x.get("creators", [])],
            "journal": x.get("publicationName"),
            "date": x.get("publicationDate"),
            "doi": x.get("doi"),
            "abstract": x.get("abstract"),
            "url": (x.get("url") or [{}])[0].get("value"),
        }
        for x in r.json().get("records", [])
    ]


@mcp.tool()
async def scopus_search(query: str, max_results: int = 10) -> list[dict]:
    """Search Elsevier Scopus. Example: 'TITLE-ABS-KEY(deep learning)' or plain keywords."""
    q = query if "(" in query else f"TITLE-ABS-KEY({query})"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(
            "https://api.elsevier.com/content/search/scopus",
            params={"query": q, "count": max_results},
            headers={"X-ELS-APIKey": _key("ELSEVIER_API_KEY"), "Accept": "application/json"},
        )
        r.raise_for_status()
    return [
        {
            "title": x.get("dc:title"),
            "first_author": x.get("dc:creator"),
            "journal": x.get("prism:publicationName"),
            "date": x.get("prism:coverDate"),
            "doi": x.get("prism:doi"),
            "cited_by": x.get("citedby-count"),
        }
        for x in r.json().get("search-results", {}).get("entry", [])
    ]


@mcp.tool()
async def ieee_search(query: str, max_results: int = 10) -> list[dict]:
    """Search IEEE Xplore articles by keyword."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(
            "https://ieeexploreapi.ieee.org/api/v1/search/articles",
            params={"apikey": _key("IEEE_API_KEY"), "querytext": query, "max_records": max_results},
        )
        r.raise_for_status()
    return [
        {
            "title": x.get("title"),
            "authors": [a.get("full_name") for a in x.get("authors", {}).get("authors", [])],
            "venue": x.get("publication_title"),
            "year": x.get("publication_year"),
            "doi": x.get("doi"),
            "abstract": x.get("abstract"),
            "url": x.get("html_url"),
        }
        for x in r.json().get("articles", [])
    ]


class AuthMiddleware:
    """Require MCP_ACCESS_KEY via 'Authorization: Bearer <key>' or '?key=<key>'."""

    def __init__(self, app):
        self.app = app
        self.key = os.getenv("MCP_ACCESS_KEY")
        if not self.key:
            raise RuntimeError("Set MCP_ACCESS_KEY before starting the server")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            headers = dict(scope["headers"])
            bearer = headers.get(b"authorization", b"").decode()
            query = scope.get("query_string", b"").decode()
            ok = hmac.compare_digest(bearer, f"Bearer {self.key}") or any(
                hmac.compare_digest(p, f"key={self.key}") for p in query.split("&")
            )
            if not ok:
                await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app():
    app = mcp.streamable_http_app()
    app.add_route("/health", lambda request: PlainTextResponse("ok"))
    return AuthMiddleware(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
