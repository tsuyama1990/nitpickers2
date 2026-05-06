import json
import os
from typing import Any

import httpx
from dotenv import load_dotenv

from src.config import settings
from src.utils import logger


class JulesApiError(Exception):
    pass


class JulesApiClient:
    BASE_URL = settings.jules.base_url

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key: str | None = api_key or settings.JULES_API_KEY.get_secret_value()
        if not self.api_key:
            load_dotenv()
            self.api_key = os.getenv("JULES_API_KEY") or os.getenv("GOOGLE_API_KEY")

        if not self.api_key:
            self._try_load_key_from_env_file()

        if not self.api_key:
            self._ensure_api_key_or_raise()

        # We store the api key securely on the object.
        # We will dynamically build headers in requests to prevent leak.
        self._api_key = str(self.api_key or "")

    def _try_load_key_from_env_file(self) -> None:
        env_file_path = settings.paths.workspace_root / ".env"
        try:
            if env_file_path.exists():
                content = env_file_path.read_text()
                for line in content.splitlines():
                    key_part = line.split("=", 1)[0].strip()
                    if key_part in ["JULES_API_KEY", "GOOGLE_API_KEY"]:
                        parts = line.split("=", 1)
                        if len(parts) > 1:
                            candidate = parts[1].strip().strip('"').strip("'")
                            if candidate:
                                self.api_key = candidate
                                return
        except Exception:
            logger.debug("Skipping malformed .env line during key check.")

    def _ensure_api_key_or_raise(self) -> None:
        msg = (
            "API Key not found for Jules API. "
            "Please set JULES_API_KEY or GOOGLE_API_KEY in your .env file or environment variables. "
            "Note: If you have the variable in .env, ensure it is not empty."
        )
        raise ValueError(msg)

    def _get_headers(self) -> dict[str, str]:
        """Returns headers for Jules API, including model version selection."""
        headers = {
            "Content-Type": "application/json",
        }
        # Only add x-goog-api-key if we don't use query params (some environments prefer query params)
        headers["x-goog-api-key"] = self._api_key

        # Add model version header if configured
        if settings.jules.model:
            headers["x-goog-agent-version"] = settings.jules.model
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        base_url = self.BASE_URL.rstrip("/")
        endpoint = endpoint.lstrip("/")
        url = f"{base_url}/{endpoint}" if not endpoint.startswith("http") else endpoint

        try:
            with httpx.Client(timeout=settings.jules.request_timeout) as client:
                response = client.request(
                    method,
                    url,
                    headers=self._get_headers(),
                    json=data,
                    params=params,
                )

                response.raise_for_status()

                resp_body = response.text
                return dict(json.loads(resp_body)) if resp_body else {}

        except httpx.HTTPStatusError as e:
            err_msg = e.response.text
            if e.response.status_code == 404:
                msg = f"404 Not Found: {url}\nResponse body: {err_msg}"
                raise JulesApiError(msg) from e
            logger.error(f"Jules API Error {e.response.status_code}: {err_msg}")
            emsg = f"API request failed: {e.response.status_code} {err_msg}"
            raise JulesApiError(emsg) from e
        except Exception as e:
            logger.error(f"Network Error: {e}")
            emsg = f"Network request failed: {e}"
            raise JulesApiError(emsg) from e

    async def _request_async(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Async version of _request with aggressive retry strategies for 404s."""
        base_url = self.BASE_URL.rstrip("/")
        endpoint = endpoint.lstrip("/")
        url = f"{base_url}/{endpoint}" if not endpoint.startswith("http") else endpoint

        # Prepare headers and params
        headers = self._get_headers()
        req_params = params.copy() if params else {}

        async def _do_req(target_url: str, use_h2: bool = True) -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=settings.jules.request_timeout, http2=use_h2
            ) as client:
                return await client.request(
                    method,
                    target_url,
                    headers=headers,
                    json=data,
                    params=req_params,
                )

        try:
            response = await _do_req(url)

            # 404 Handling: Try various workarounds
            if response.status_code == 404:
                logger.info(f"404 Not Found for {url}. Attempting workarounds...")

                # Workaround 1: Trailing slash (sometimes required for collections)
                if not url.endswith("/") and "/" not in endpoint:
                    url_slash = f"{url}/"
                    logger.debug(f"Retry 1: Trailing slash -> {url_slash}")
                    response = await _do_req(url_slash)

                # Workaround 2: Key in query parameter (fixes some proxy/auth issues)
                if response.status_code == 404 and "key=" not in str(url):
                    separator = "&" if "?" in str(url) else "?"
                    url_key = f"{url}{separator}key={self._api_key}"
                    logger.debug(f"Retry 2: Key in query param -> {url_key[:50]}...")
                    response = await _do_req(url_key)

                # Workaround 3: Force HTTP/1.1 (fixes some h2 multiplexing issues)
                if response.status_code == 404:
                    logger.debug("Retry 3: Forcing HTTP/1.1")
                    response = await _do_req(url, use_h2=False)

            response.raise_for_status()
            resp_body = response.text
            return dict(json.loads(resp_body)) if resp_body else {}

        except httpx.HTTPStatusError as e:
            err_msg = e.response.text
            if e.response.status_code == 404:
                msg = f"404 Not Found: {url}\nResponse body: {err_msg}"
                raise JulesApiError(msg) from e
            logger.error(f"Jules API Error {e.response.status_code}: {err_msg}")
            emsg = f"API request failed: {e.response.status_code} {err_msg}"
            raise JulesApiError(emsg) from e
        except Exception as e:
            logger.error(f"Network Error: {e}")
            emsg = f"Network request failed: {e}"
            raise JulesApiError(emsg) from e

    def list_sources(self) -> list[dict[str, Any]]:
        data = self._request("GET", "sources")
        return list(data.get("sources", []))

    def find_source_by_repo(self, repo_name: str) -> str | None:
        sources = self.list_sources()
        for src in sources:
            if repo_name in str(src.get("name", "")):
                return str(src["name"])
        return None

    async def create_session(
        self,
        source: str,
        prompt: str,
        require_plan_approval: bool = False,
        branch: str | None = None,
        title: str | None = None,
        automation_mode: str = "AUTO_CREATE_PR",
    ) -> dict[str, Any]:
        """Creates a new Jules session. Async to avoid blocking the event loop."""
        payload = {
            "prompt": prompt,
            "sourceContext": {
                "source": source,
                "githubRepoContext": {"startingBranch": branch},
            },
            "requirePlanApproval": require_plan_approval,
            "automationMode": automation_mode,
        }
        if title:
            payload["title"] = title
        return await self._request_async("POST", "sessions", payload)

    def approve_plan(self, session_id: str, plan_id: str) -> dict[str, Any]:
        """Approves the current plan in the session, triggering implementation."""
        # Ensure session_id is just the name if it's a full URL
        if session_id.startswith("http") and "/sessions/" in session_id:
            session_id = "sessions/" + session_id.rsplit("/sessions/", maxsplit=1)[-1]

        endpoint = f"{session_id}:approvePlan"
        payload: dict[str, Any] = {}
        return self._request("POST", endpoint, payload)

    def list_activities(self, session_id_path: str) -> list[dict[str, Any]]:
        all_activities = []
        page_token = ""

        # If session_id_path is a full URL, extract the path part or use it directly
        if session_id_path.startswith("http") and "/sessions/" in session_id_path:
            session_id_path = "sessions/" + session_id_path.rsplit("/sessions/", maxsplit=1)[-1]
        elif session_id_path.startswith("http"):
            logger.warning(f"Unexpected session URL format: {session_id_path}")

        try:
            while True:
                endpoint = f"{session_id_path}/{settings.jules.activities_path}"
                params = {"pageSize": str(settings.jules.page_size)}
                if page_token:
                    params["pageToken"] = page_token

                resp = self._request("GET", endpoint, params=params)
                acts = list(resp.get("activities", []))
                if not acts:
                    break
                all_activities.extend(acts)

                page_token = resp.get("nextPageToken", "")
                if not page_token:
                    break
        except JulesApiError as e:
            if "404" in str(e):
                return []
            raise

        return all_activities

    async def list_activities_async(self, session_id_path: str) -> list[dict[str, Any]]:
        """Async version of list_activities using httpx to avoid blocking the event loop."""

        all_activities: list[dict[str, Any]] = []
        page_token = ""

        # If session_id_path is a full URL, extract the path part
        if session_id_path.startswith("http") and "/sessions/" in session_id_path:
            session_id_path = "sessions/" + session_id_path.rsplit("/sessions/", maxsplit=1)[-1]
        elif session_id_path.startswith("http"):
            logger.warning(f"Unexpected session URL format: {session_id_path}")

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                while True:
                    url = f"{self.BASE_URL}/{session_id_path}/{settings.jules.activities_path}"
                    params = {"pageSize": str(settings.jules.page_size)}
                    if page_token:
                        params["pageToken"] = page_token

                    resp = await client.get(
                        url, params=params, headers=self._get_headers(), timeout=30.0
                    )
                    if resp.status_code == 404:
                        # Session may be newly created and activities not yet propagated
                        break
                    if resp.status_code != 200:
                        logger.warning(
                            f"list_activities_async: unexpected status {resp.status_code}"
                        )
                        break

                    data = resp.json()
                    acts: list[dict[str, Any]] = data.get("activities", [])
                    if not acts:
                        break
                    all_activities.extend(acts)

                    page_token = str(data.get("nextPageToken", ""))
                    if not page_token:
                        break
        except Exception as e:
            logger.warning(f"list_activities_async failed: {e}")

        return all_activities
