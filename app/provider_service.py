from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from app.auth import is_masked_secret
from app.providers import (
    normalize_provider_base_url,
    provider_get_json,
    provider_test_error_detail,
    provider_test_response,
    voice_ids_from_payload,
)
from app.repositories import UnitOfWork


_WORKFLOW_NODE_LIMIT = 2048
_WORKFLOW_BYTE_LIMIT = 2_000_000
_OBJECT_INFO_BYTE_LIMIT = 16_000_000
_ASSET_INPUT_MARKERS = (
    "checkpoint",
    "ckpt",
    "clip",
    "control_net",
    "controlnet",
    "insightface",
    "instantid",
    "ipadapter",
    "lora",
    "model",
    "photomaker",
    "pulid",
    "unet",
    "vae",
)
_IDENTITY_APPLICATION_MARKERS = (
    "faceid",
    "faceidentity",
    "identity",
    "instantid",
    "ipadapter",
    "photomaker",
    "pulid",
)
_IDENTITY_NON_APPLICATION_MARKERS = (
    "detector",
    "loader",
    "preprocessor",
    "provider",
)
_IDENTITY_APPLICATION_OUTPUTS = {"CONDITIONING", "MODEL"}


def basic_auth_headers(value: str | None) -> dict:
    raw = str(value or "").strip()
    if not raw:
        return {}
    return {"Authorization": f"Basic {base64.b64encode(raw.encode()).decode('ascii')}"}


def _workflow_nodes(workflow_patch: dict) -> dict[str, dict]:
    if not isinstance(workflow_patch, dict) or not workflow_patch:
        raise ValueError("Choose a non-empty ComfyUI API-format workflow.")
    if len(workflow_patch) > _WORKFLOW_NODE_LIMIT:
        raise ValueError("The workflow contains too many nodes to inspect safely.")
    try:
        encoded_size = len(json.dumps(workflow_patch, separators=(",", ":"), ensure_ascii=False).encode())
    except (RecursionError, TypeError, ValueError) as exc:
        raise ValueError("The workflow must contain JSON-compatible values.") from exc
    if encoded_size > _WORKFLOW_BYTE_LIMIT:
        raise ValueError("The workflow is too large to inspect safely.")
    result = {}
    for raw_node_id, raw_node in workflow_patch.items():
        node_id = str(raw_node_id or "").strip()
        if not node_id or len(node_id) > 100:
            raise ValueError("The workflow contains an invalid node ID.")
        if not isinstance(raw_node, dict):
            raise ValueError(f"Workflow node {node_id} must be an object.")
        class_type = str(raw_node.get("class_type") or "").strip()
        inputs = raw_node.get("inputs")
        if not class_type or len(class_type) > 240 or not isinstance(inputs, dict):
            raise ValueError(f"Workflow node {node_id} needs a class_type and inputs object.")
        result[node_id] = {**raw_node, "class_type": class_type, "inputs": inputs}
    return result


def _workflow_inspection_result(
    *,
    status: str,
    message: str,
    provider_compatible: bool = False,
    identity_input_candidates: list[dict] | None = None,
    detected_node_types: list[str] | None = None,
    missing_node_types: list[str] | None = None,
    asset_checks: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "provider": "comfyui",
        "status": status,
        "provider_compatible": provider_compatible,
        "live_tested": False,
        "message": message,
        "identity_input_candidates": identity_input_candidates or [],
        "detected_node_types": detected_node_types or [],
        "missing_node_types": missing_node_types or [],
        "asset_checks": asset_checks or [],
        "warnings": warnings or [],
    }


def _input_specs(provider_node: dict) -> tuple[dict, dict]:
    schema = provider_node.get("input")
    schema = schema if isinstance(schema, dict) else {}
    required = schema.get("required")
    optional = schema.get("optional")
    required = required if isinstance(required, dict) else {}
    optional = optional if isinstance(optional, dict) else {}
    return required, {**required, **optional}


def _link_reference(value) -> tuple[str, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    source, output_index = value
    if isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
        return None
    if isinstance(source, bool) or not isinstance(source, (str, int)):
        return None
    source_id = str(source).strip()
    return (source_id, output_index) if source_id else None


def _provider_outputs(provider_node: dict) -> list[str]:
    outputs = provider_node.get("output")
    if not isinstance(outputs, (list, tuple)):
        return []
    return [str(value).strip().upper() for value in outputs]


def _link_types_match(spec, source_type: str) -> bool:
    if not isinstance(spec, (list, tuple)) or not spec or not isinstance(spec[0], str):
        return True
    expected = spec[0].strip().upper()
    return not expected or expected in {"*", "ANY"} or source_type in {"*", "ANY", expected}


def _identity_application_node(node_type: str, provider_node: dict) -> bool:
    normalized = "".join(character for character in node_type.casefold() if character.isalnum())
    if not any(marker in normalized for marker in _IDENTITY_APPLICATION_MARKERS):
        return False
    if any(marker in normalized for marker in _IDENTITY_NON_APPLICATION_MARKERS):
        return False
    return bool(set(_provider_outputs(provider_node)) & _IDENTITY_APPLICATION_OUTPUTS)


def _reachable(adjacency: dict[str, set[str]], start: str) -> set[str]:
    found = {start}
    pending = [start]
    while pending:
        node_id = pending.pop()
        for child in adjacency.get(node_id, set()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def _has_cycle(adjacency: dict[str, set[str]]) -> bool:
    indegree = {node_id: 0 for node_id in adjacency}
    for children in adjacency.values():
        for child in children:
            indegree[child] = indegree.get(child, 0) + 1
    pending = [node_id for node_id, count in indegree.items() if count == 0]
    visited = 0
    while pending:
        node_id = pending.pop()
        visited += 1
        for child in adjacency.get(node_id, set()):
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
    return visited != len(indegree)


def _bounded_warnings(messages: list[str], limit: int = 20) -> list[str]:
    unique = list(dict.fromkeys(messages))
    if len(unique) <= limit:
        return unique
    return [*unique[:limit], f"{len(unique) - limit} additional workflow structure issue(s) were omitted."]


def _inspect_comfyui_object_info(nodes: dict[str, dict], object_info: dict) -> dict:
    detected_node_types = sorted({node["class_type"] for node in nodes.values()})
    missing_node_types = sorted(node_type for node_type in detected_node_types if node_type not in object_info)
    raw_candidates = []
    asset_checks = []
    structural_issues = []
    adjacency = {node_id: set() for node_id in nodes}
    output_nodes = set()
    identity_nodes = set()

    for node_id, node in nodes.items():
        node_type = node["class_type"]
        provider_node = object_info.get(node_type)
        if not isinstance(provider_node, dict):
            continue
        required, specs = _input_specs(provider_node)
        if provider_node.get("output_node") is True:
            output_nodes.add(node_id)
        if _identity_application_node(node_type, provider_node):
            identity_nodes.add(node_id)
        missing_inputs = sorted(str(name) for name in required if name not in node["inputs"])
        if missing_inputs:
            structural_issues.append(
                f"Node {node_id} ({node_type}) is missing required input(s): {', '.join(missing_inputs)}."
            )
        metadata = node.get("_meta")
        metadata = metadata if isinstance(metadata, dict) else {}
        title = str(metadata.get("title") or provider_node.get("display_name") or node_type).strip()
        for raw_input_name, current_value in node["inputs"].items():
            input_name = str(raw_input_name)
            spec = specs.get(raw_input_name)
            if not isinstance(spec, (list, tuple)) or not spec:
                structural_issues.append(f"Node {node_id} ({node_type}) contains provider-unknown input {input_name}.")
                continue
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            is_upload = bool(options.get("image_upload")) or (node_type == "LoadImage" and input_name == "image")
            if is_upload:
                raw_candidates.append(
                    {
                        "node_id": node_id,
                        "input_name": input_name,
                        "label": f"{title} (node {node_id})",
                    }
                )
            allowed = spec[0] if isinstance(spec[0], list) else None
            if (
                allowed is not None
                and isinstance(current_value, (str, int, float, bool))
                and _is_asset_input(input_name)
            ):
                asset_checks.append(
                    {
                        "node_id": node_id,
                        "node_type": node_type,
                        "input_name": input_name,
                        "value": str(current_value),
                        "available": current_value in allowed
                        or str(current_value) in {str(value) for value in allowed},
                    }
                )
            link = _link_reference(current_value)
            if not link:
                continue
            source_id, output_index = link
            source = nodes.get(source_id)
            if not source:
                structural_issues.append(
                    f"Node {node_id} ({node_type}) input {input_name} links to missing node {source_id}."
                )
                continue
            source_schema = object_info.get(source["class_type"])
            if not isinstance(source_schema, dict):
                continue
            outputs = _provider_outputs(source_schema)
            if output_index >= len(outputs):
                structural_issues.append(
                    f"Node {node_id} ({node_type}) input {input_name} uses missing output {output_index} "
                    f"from node {source_id}."
                )
                continue
            source_type = outputs[output_index]
            if not _link_types_match(spec, source_type):
                expected = str(spec[0]).strip().upper()
                structural_issues.append(
                    f"Node {node_id} ({node_type}) input {input_name} expects {expected}, "
                    f"but node {source_id} output {output_index} is {source_type}."
                )
                continue
            adjacency[source_id].add(node_id)

    if not output_nodes:
        structural_issues.append("The workflow contains no provider-reported output node.")
    if not identity_nodes:
        structural_issues.append(
            "The workflow contains no provable identity application node that outputs MODEL or CONDITIONING."
        )
    if _has_cycle(adjacency):
        structural_issues.append("The workflow graph contains a cycle.")

    candidates = []
    for candidate in raw_candidates:
        downstream = _reachable(adjacency, candidate["node_id"])
        connected_identity_nodes = downstream & identity_nodes
        if any(output_nodes & _reachable(adjacency, identity_node) for identity_node in connected_identity_nodes):
            candidates.append(candidate)
    raw_candidates.sort(key=lambda item: (item["label"].casefold(), item["node_id"], item["input_name"]))
    candidates.sort(key=lambda item: (item["label"].casefold(), item["node_id"], item["input_name"]))
    asset_checks.sort(key=lambda item: (item["node_id"], item["input_name"]))
    unavailable_assets = [item for item in asset_checks if not item["available"]]
    warnings = [
        "Structural provider metadata inspection does not run the workflow or prove that the generated face matches.",
    ]
    if missing_node_types:
        warnings.append(f"ComfyUI does not report {len(missing_node_types)} workflow node type(s).")
    if unavailable_assets:
        warnings.append(f"ComfyUI does not report {len(unavailable_assets)} configured model asset(s).")
    if not raw_candidates:
        warnings.append("No provider-reported image upload input is available for the persona reference.")
    elif not candidates:
        warnings.append(
            "No image upload input has a valid path through an identity application node to an output node."
        )
    warnings.extend(_bounded_warnings(structural_issues))
    compatible = not missing_node_types and not unavailable_assets and not structural_issues and bool(candidates)
    message = (
        "ComfyUI reports installed nodes and assets, complete required inputs, valid typed links, an output path, "
        "and a reference path through an identity application node. A live generation test is still required."
        if compatible
        else "The workflow remains a draft because its executable reference-conditioned path could not be proven."
    )
    return _workflow_inspection_result(
        status="provider_compatible" if compatible else "incompatible",
        provider_compatible=compatible,
        message=message,
        identity_input_candidates=candidates,
        detected_node_types=detected_node_types,
        missing_node_types=missing_node_types,
        asset_checks=asset_checks,
        warnings=warnings,
    )


def _is_asset_input(input_name: str) -> bool:
    normalized = input_name.casefold().replace("-", "_")
    return any(marker in normalized for marker in _ASSET_INPUT_MARKERS)


class ProviderService:
    def __init__(self, session_factory, secret_store, config, registry, logger, provider_url_policy=None):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.config = config
        self.registry = registry
        self.logger = logger
        self.provider_url_policy = provider_url_policy

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def models(self) -> list[str]:
        return self.registry.models()

    def inspect_comfyui_workflow(
        self,
        user_id: str,
        workflow_patch: dict,
        overrides: dict | None = None,
    ) -> dict:
        """Inspect provider metadata without executing or saving a workflow."""
        try:
            nodes = _workflow_nodes(workflow_patch)
        except ValueError as exc:
            return _workflow_inspection_result(
                status="invalid",
                message=str(exc),
                warnings=["The workflow was not sent to ComfyUI."],
            )

        node_types = sorted({node["class_type"] for node in nodes.values()})
        try:
            effective = self._effective_settings(user_id, overrides)
            base = normalize_provider_base_url(
                effective.get("image_local_base_url"),
                self.config.comfyui_base_url,
            )
            if self.provider_url_policy:
                base = self.provider_url_policy.normalize(base, label="ComfyUI")
            object_info = provider_get_json(
                f"{base}/object_info",
                headers=basic_auth_headers(effective.get("image_local_api_auth")),
                timeout=self.config.provider_timeout_seconds,
                max_bytes=_OBJECT_INFO_BYTE_LIMIT,
            )
            if not isinstance(object_info, dict):
                raise ValueError("ComfyUI returned invalid object metadata.")
        except urllib.error.HTTPError:
            result = _workflow_inspection_result(
                status="error",
                message="ComfyUI returned an error while workflow compatibility was inspected.",
                detected_node_types=node_types,
                warnings=["No workflow was run or saved."],
            )
        except urllib.error.URLError:
            result = _workflow_inspection_result(
                status="unreachable",
                message="ComfyUI is not reachable, so workflow compatibility could not be inspected.",
                detected_node_types=node_types,
                warnings=["No workflow was run or saved."],
            )
        except ValueError:
            result = _workflow_inspection_result(
                status="invalid",
                message="ComfyUI configuration or object metadata is invalid.",
                detected_node_types=node_types,
                warnings=["No workflow was run or saved."],
            )
        except Exception:  # noqa: BLE001 - safe, content-free provider diagnostics
            result = _workflow_inspection_result(
                status="error",
                message="ComfyUI workflow compatibility inspection failed.",
                detected_node_types=node_types,
                warnings=["No workflow was run or saved."],
            )
        else:
            result = _inspect_comfyui_object_info(nodes, object_info)
        self.logger.info(
            "comfyui workflow inspection status=%s nodes=%s missing_types=%s candidates=%s",
            result["status"],
            len(nodes),
            len(result["missing_node_types"]),
            len(result["identity_input_candidates"]),
        )
        return result

    def _effective_settings(self, user_id: str, overrides: dict | None = None) -> dict:
        with self._uow() as uow:
            settings = uow.repo.settings(user_id) or {"preferences": {}}
        effective = {**settings, **(settings.get("preferences") or {})}
        incoming = overrides or {}
        preferences = incoming.get("preferences")
        if isinstance(preferences, dict):
            effective.update(preferences)
        effective.update({key: value for key, value in incoming.items() if key != "preferences"})
        return effective

    def check(self, user_id: str, provider: str, overrides: dict | None = None) -> dict | None:
        provider = str(provider or "").strip().lower()
        if provider == "a1111":
            provider = "automatic1111"
        if provider not in {"ollama", "openai", "kokoro", "automatic1111", "comfyui"}:
            return None
        effective = self._effective_settings(user_id, overrides)
        incoming = overrides or {}
        key = incoming.get("openai_api_key")
        if key and not is_masked_secret(key):
            effective["openai_api_key"] = key
        label = {
            "ollama": "Ollama",
            "openai": "OpenAI",
            "kokoro": "Kokoro",
            "automatic1111": "Automatic1111",
            "comfyui": "ComfyUI",
        }[provider]
        try:
            if provider == "ollama":
                health = self.registry.chat("ollama").health()
                result = provider_test_response(
                    provider,
                    health.ok,
                    health.status.value,
                    health.message,
                    health.detail,
                )
            elif provider == "openai":
                api_key = str(effective.get("openai_api_key") or "").strip()
                if not api_key or is_masked_secret(api_key):
                    return provider_test_response(provider, False, "missing", "OpenAI API key is not configured.")
                request = urllib.request.Request(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=self.config.provider_timeout_seconds) as response:
                    import json

                    payload = json.loads(response.read().decode())
                models = payload.get("data", []) if isinstance(payload, dict) else []
                result = provider_test_response(
                    provider,
                    True,
                    "ready",
                    "OpenAI is reachable.",
                    f"{len(models)} model(s) visible.",
                )
            elif provider == "kokoro":
                base = normalize_provider_base_url(
                    effective.get("tts_local_base_url"),
                    "http://127.0.0.1:8880",
                )
                if self.provider_url_policy:
                    base = self.provider_url_policy.normalize(base, label="Kokoro")
                payload = provider_get_json(
                    f"{base}/v1/audio/voices",
                    timeout=self.config.provider_timeout_seconds,
                )
                voices = voice_ids_from_payload(payload)
                result = provider_test_response(
                    provider,
                    True,
                    "ready",
                    "Kokoro is reachable.",
                    f"{len(voices)} voice(s) available.",
                )
            else:
                default = (
                    self.config.automatic1111_base_url if provider == "automatic1111" else self.config.comfyui_base_url
                )
                base = normalize_provider_base_url(effective.get("image_local_base_url"), default)
                if self.provider_url_policy:
                    base = self.provider_url_policy.normalize(base, label=label)
                endpoint = "/sdapi/v1/options" if provider == "automatic1111" else "/system_stats"
                provider_get_json(
                    f"{base}{endpoint}",
                    headers=basic_auth_headers(effective.get("image_local_api_auth")),
                    timeout=self.config.provider_timeout_seconds,
                )
                result = provider_test_response(provider, True, "ready", f"{label} is reachable.")
        except ValueError as exc:
            result = provider_test_response(provider, False, "invalid", f"{label} configuration is invalid.", str(exc))
        except urllib.error.HTTPError as exc:
            result = provider_test_response(
                provider,
                False,
                "failed",
                f"{label} responded with an error.",
                provider_test_error_detail(exc),
            )
        except urllib.error.URLError as exc:
            result = provider_test_response(
                provider,
                False,
                "unreachable",
                f"{label} is not reachable.",
                provider_test_error_detail(exc),
            )
        except Exception as exc:  # noqa: BLE001 - safe readiness diagnostics
            result = provider_test_response(
                provider,
                False,
                "error",
                f"{label} test failed.",
                provider_test_error_detail(exc),
            )
        self.logger.info("provider readiness provider=%s status=%s", provider, result.get("status"))
        return result
