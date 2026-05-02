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
            "x-goog-api-key": self._api_key,
            "Content-Type": settings.jules.content_type,
        }
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
        url = endpoint if endpoint.startswith("http") else f"{self.BASE_URL}/{endpoint}"

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
            if e.response.status_code == 404:
                msg = f"404 Not Found: {url}"
                raise JulesApiError(msg) from e
            err_msg = e.response.text
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
        """Async version of _request using httpx.AsyncClient to avoid blocking the event loop."""
        url = endpoint if endpoint.startswith("http") else f"{self.BASE_URL}/{endpoint}"

        try:
            async with httpx.AsyncClient(timeout=settings.jules.request_timeout) as client:
                response = await client.request(
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
            if e.response.status_code == 404:
                msg = f"404 Not Found: {url}"
                raise JulesApiError(msg) from e
            err_msg = e.response.text
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
